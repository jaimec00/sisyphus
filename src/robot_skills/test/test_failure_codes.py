# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Tests for the D17 ownership split of FailureCode.

"Can't be done" is a backend refusal; "unsafe to continue" is a safety-layer
clamp or abort.  The split is what lets a consumer decide *whose* limit it hit
without parsing prose, so it has to be total and unambiguous.
"""

from robot_skills import BACKEND_REFUSAL_CODES, FailureCode, SAFETY_EVENT_CODES


def test_every_failure_code_belongs_to_exactly_one_owner():
    """A code with no owner (or two) would leave a consumer unable to branch."""
    both = BACKEND_REFUSAL_CODES & SAFETY_EVENT_CODES
    assert both == frozenset(), f'codes claimed by both layers: {both}'

    unclassified = set(FailureCode) - (BACKEND_REFUSAL_CODES | SAFETY_EVENT_CODES)
    assert unclassified == set(), (
        f'unclassified FailureCode member(s): {sorted(c.name for c in unclassified)} -- '
        'add each to BACKEND_REFUSAL_CODES or SAFETY_EVENT_CODES (D17)')


def test_the_two_owner_sets_hold_the_expected_codes():
    """Pin the classification itself, so a reshuffle is a deliberate edit."""
    assert {code.value for code in BACKEND_REFUSAL_CODES} == {
        'unknown_location',
        'unknown_object',
        'not_graspable',
        'object_already_held',
        'gripper_occupied',
        'gripper_empty',
        'out_of_reach',
        'out_of_range',
        'unsupported_skill',
    }
    assert {code.value for code in SAFETY_EVENT_CODES} == {'rejected'}


def test_a_code_classifies_itself():
    """Consumers branch on the code they already hold, not on an import of a set."""
    assert FailureCode.OUT_OF_REACH.is_backend_refusal is True
    assert FailureCode.OUT_OF_REACH.is_safety_event is False
    assert FailureCode.REJECTED.is_safety_event is True
    assert FailureCode.REJECTED.is_backend_refusal is False

    for code in FailureCode:
        assert code.is_backend_refusal != code.is_safety_event, (
            f'{code.name} is neither or both')


def test_a_precondition_failure_is_a_refusal_not_a_safety_event():
    """gripper_empty is checked before motion, so it is the backend's "no"."""
    assert FailureCode.GRIPPER_EMPTY.is_backend_refusal is True
    assert FailureCode.GRIPPER_EMPTY.is_safety_event is False


def test_the_owner_sets_are_immutable():
    """A consumer cannot reclassify a code for everyone else by mutating a set."""
    assert isinstance(BACKEND_REFUSAL_CODES, frozenset)
    assert isinstance(SAFETY_EVENT_CODES, frozenset)
