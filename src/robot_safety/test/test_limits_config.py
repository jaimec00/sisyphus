# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Acceptance criterion: limits load from YAML, with documented defaults.

Two jobs here.  First, the *shipped* ``limits.yaml`` -- it must be packaged
(readable through ``importlib.resources``, from a source checkout and from a
symlink-installed build alike), it must parse, and every field must come out
populated.  Second, the parser -- a limits file that is wrong must fail loudly
at load, because a safety limit that silently fell back to a default is the one
failure mode this layer cannot have.
"""

from importlib import resources
from math import inf, nan

import pytest
from robot_safety import (
    DEFAULT_LIMITS_RESOURCE,
    KeepOutBox,
    MotionAxis,
    SafetyConfigError,
    SafetyLimits,
)
from safety_fixtures import limits_mapping, make_limits
import yaml


def shipped_text() -> str:
    """Return the shipped limits file's text, read the way the code reads it."""
    resource = resources.files('robot_safety') / DEFAULT_LIMITS_RESOURCE
    return resource.read_text(encoding='utf-8')


def test_the_shipped_limits_file_is_packaged_beside_the_code():
    """The YAML must travel with the importable package, not in a share/ dir.

    This is the packaging contract from ``setup.py``'s ``package_data``: it is
    what makes ``SafetyLimits.defaults()`` work with no ament index, no ROS
    graph and no rebuild step between editing the file and reading it.
    """
    resource = resources.files('robot_safety') / DEFAULT_LIMITS_RESOURCE
    assert resource.is_file(), f'{DEFAULT_LIMITS_RESOURCE} is not packaged with robot_safety'
    assert 'max_force' in shipped_text()


def test_shipped_defaults_parse_and_populate_every_field():
    """Every limit the layer consults is present and physically sane."""
    limits = SafetyLimits.defaults()

    assert limits.column.min_height < limits.column.max_height
    # Every axis of MotionAxis is capped: an uncapped axis is an unchecked one.
    assert set(limits.motion.velocities) == set(MotionAxis)
    assert all(limits.motion.velocity_cap(axis) > 0.0 for axis in MotionAxis)
    assert limits.motion.max_gripper_force > 0.0
    # The shipped file exercises the collision-guard seam rather than merely
    # declaring it, so a keep-out region is expected to be configured.
    assert limits.keep_out_boxes
    assert all(box.label for box in limits.keep_out_boxes)


def test_defaults_are_read_from_the_yaml_and_not_baked_into_python():
    """The file is the single source of the numbers (no Python constants)."""
    raw = yaml.safe_load(shipped_text())
    limits = SafetyLimits.defaults()

    assert limits.column.min_height == raw['column']['min_height']
    assert limits.column.max_height == raw['column']['max_height']
    assert limits.motion.max_gripper_force == raw['gripper']['max_force']
    for axis in MotionAxis:
        assert limits.motion.velocity_cap(axis) == raw['velocity'][axis.value]

    # ...and a file carrying different numbers yields different limits, which a
    # hard-coded default would quietly override.
    raw['column']['max_height'] += 0.5
    raw['gripper']['max_force'] += 7.0
    retuned = SafetyLimits.from_yaml(yaml.safe_dump(raw))
    assert retuned.column.max_height == limits.column.max_height + 0.5
    assert retuned.motion.max_gripper_force == limits.motion.max_gripper_force + 7.0


def test_shipped_defaults_are_documented_in_the_file_itself():
    """Every configured section carries a comment explaining its numbers.

    A safety limit with no recorded rationale is a number nobody dares change;
    the file is the place that reasoning has to live.
    """
    text = shipped_text()
    comment_lines = [line for line in text.splitlines() if line.lstrip().startswith('#')]

    assert len(comment_lines) > 20, 'the shipped limits file lost its rationale'
    for section in ('column', 'velocity', 'gripper'):
        assert f'{section}:' in text


def test_defaults_are_cached_and_immutable():
    """Repeated loads are cheap and cannot be mutated by a previous caller."""
    first = SafetyLimits.defaults()
    second = SafetyLimits.defaults()

    assert first is second
    with pytest.raises(TypeError):
        first.motion.velocities[MotionAxis.BASE] = 99.0


def test_yaml_text_round_trips_into_a_limit_set():
    """Loading from YAML text produces the same values as loading a mapping."""
    text = yaml.safe_dump(limits_mapping())

    assert SafetyLimits.from_yaml(text) == make_limits()


@pytest.mark.parametrize(
    'overrides, expected',
    [
        pytest.param({'column': {'min_height': 0.0}}, 'max_height', id='missing-column-key'),
        pytest.param(
            {'column': {'min_height': 1.0, 'max_height': 1.0}},
            'must be below',
            id='inverted-column-range'),
        pytest.param(
            {'column': {'min_height': 0.0, 'max_height': nan}},
            'max_height',
            id='non-finite-column-bound'),
        pytest.param(
            {'velocity': {'base': 1.0, 'arm': 1.0}}, 'column', id='missing-axis-cap'),
        pytest.param(
            {'velocity': {'base': 1.0, 'column': 1.0, 'arm': 1.0, 'wrist': 1.0}},
            'unknown key',
            id='unknown-axis'),
        pytest.param(
            {'velocity': {'base': -1.0, 'column': 1.0, 'arm': 1.0}},
            'positive',
            id='negative-velocity-cap'),
        pytest.param(
            {'velocity': {'base': 0.0, 'column': 1.0, 'arm': 1.0}},
            'positive',
            id='zero-velocity-cap'),
        pytest.param(
            {'velocity': {'base': inf, 'column': 1.0, 'arm': 1.0}},
            'finite',
            id='infinite-velocity-cap'),
        pytest.param({'gripper': {'max_force': -5.0}}, 'positive', id='negative-force-cap'),
        pytest.param({'gripper': {}}, 'max_force', id='missing-force-cap'),
        pytest.param({'gripper': 40.0}, 'expected a mapping', id='scalar-section'),
        pytest.param({'keep_out_boxes': {}}, 'expected a list', id='boxes-not-a-list'),
        pytest.param(
            {'keep_out_boxes': [{'label': 'nowhere'}]},
            'at least one bound',
            id='unbounded-box'),
        pytest.param(
            {'keep_out_boxes': [{'label': 'bad', 'z_min': 1.0, 'z_max': 0.0}]},
            'must be below',
            id='inverted-box'),
        pytest.param(
            {'keep_out_boxes': [{'label': 'typo', 'zmax': 1.0}]},
            'unknown key',
            id='misspelled-box-bound'),
        pytest.param(
            {'keep_out_boxes': [{'z_max': 1.0}]}, 'label', id='unlabelled-box'),
        pytest.param(
            {
                'keep_out_boxes': [
                    {'label': 'twice', 'z_max': 1.0},
                    {'label': 'twice', 'z_min': 2.0},
                ],
            },
            'duplicate label',
            id='duplicate-box-labels'),
    ],
)
def test_a_wrong_limits_file_fails_loudly(overrides, expected):
    """Every malformed section raises, naming the offending key."""
    with pytest.raises(SafetyConfigError, match=expected):
        SafetyLimits.from_mapping(limits_mapping(**overrides))


def test_an_unknown_top_level_section_is_rejected():
    """A typo'd section must not be read as "no such limits configured"."""
    data = limits_mapping()
    data['grippers'] = {'max_force': 5.0}

    with pytest.raises(SafetyConfigError, match='grippers'):
        SafetyLimits.from_mapping(data)


def test_a_missing_top_level_section_is_rejected():
    """A limits file with no gripper section does not silently uncap force."""
    data = limits_mapping()
    del data['gripper']

    with pytest.raises(SafetyConfigError, match='missing required key'):
        SafetyLimits.from_mapping(data)


def test_keep_out_boxes_may_be_omitted_entirely():
    """The one optional section: no boxes configured means no boxes."""
    data = limits_mapping()
    del data['keep_out_boxes']

    assert SafetyLimits.from_mapping(data).keep_out_boxes == ()


@pytest.mark.parametrize(
    'text, expected',
    [
        pytest.param('', 'empty limits file', id='empty'),
        pytest.param('column: [1, 2\n', 'not valid YAML', id='malformed'),
        pytest.param('- 1\n- 2\n', 'expected a mapping', id='sequence-at-top-level'),
    ],
)
def test_bad_yaml_text_is_rejected(text, expected):
    """Text that is not a limits document fails at load, not at first use."""
    with pytest.raises(SafetyConfigError, match=expected):
        SafetyLimits.from_yaml(text)


def test_yaml_loading_does_not_execute_python_tags():
    """``safe_load`` only: a limits file is data, never a program."""
    text = '!!python/object/apply:os.system ["true"]\n'

    with pytest.raises(SafetyConfigError, match='not valid YAML'):
        SafetyLimits.from_yaml(text)


def test_keep_out_box_bounds_are_half_open_where_a_bound_is_omitted():
    """An omitted bound means unbounded, so a box can be a half-space."""
    box = KeepOutBox.from_mapping({'label': 'below_floor', 'z_max': -0.02})

    assert box.x_min is None and box.x_max is None
    assert box.z_max == -0.02
