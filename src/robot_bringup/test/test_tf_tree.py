# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The assembled robot's TF tree is live through the bringup launch.

The essence of PR8a: ``robot_bringup`` is the first *launch* layer in this
repo, and its one job is to make the assembled robot observable in TF before
any sim exists. robot_state_publisher already proved it can *load* this
description (PR2/D29); what this launch layer adds is the real bringup
artifact and the claim that the *published tree* -- not just the model -- is
complete and correctly rooted. A launch file that expands the model fine but
spawns in the wrong namespace, forgets a frame, roots at the wrong link, or
fails to resolve the installed description would pass every model parser in
the gate and be broken here.

Everything resolves through ``get_package_share_directory`` -- the installed
copy, no source-tree fallback (D27) -- and the launch file under test is read
from the ament index too. The expected link set is *derived* from the
installed description rather than hand-maintained (D24): RSP must publish a
frame for exactly the links the URDF declares, so this gate cannot rot as the
model grows. The description is launched through the real bringup launch
(``ros2 launch robot_bringup robot.launch.py``), not re-played inline, so the
thing under test is the shipped artifact.
"""
import os
import shutil
import subprocess
import threading
import time

# rclpy / tf2_ros / urdf_parser_py are imported lazily (inside the tests) so
# that collecting this module never requires a ROS runtime; a gap in the test
# env shows up as the failing test rather than the whole collection pass.

#: A ROS domain of this suite's own, so the launched nodes do not collide
#: with anything else on the machine.
TF_DOMAIN_ID = '112'
LAUNCH_READY_TIMEOUT_S = 30.0
TF_READ_TIMEOUT_S = 30.0
#: The TF root RSP publishes for this description is the URDF root frame.
EXPECTED_ROOT = 'base_link'
#: D29: base_footprint is a *fixed child* of base_link, and odometry must be
#: published as odom -> base_link, never odom -> base_footprint. Checkable in
#: this launch layer as the invariant that base_footprint is NOT the TF root
#: and IS a direct child of the actual root.
BASE_FOOTPRINT = 'base_footprint'


def _require_tool(name):
    """Return the path to an executable on PATH, failing loudly if absent.

    Shared with the description gate's idiom (test_description.py): the tools
    (xacro, ros2) live on PATH only inside ``pixi run`` / colcon test via the
    pixi env, and a missing one should fail this test with a pointer, not a
    bare FileNotFoundError.
    """
    path = shutil.which(name)
    assert path is not None, (
        '%s is not on PATH; it is pinned in pixi.toml and declared in '
        'package.xml -- run inside `pixi run`.' % name)
    return path


def _install_share(pkg):
    from ament_index_python.packages import get_package_share_directory
    return get_package_share_directory(pkg)


def _launch_path():
    """Return the path to the installed bringup launch file."""
    return os.path.join(
        _install_share('robot_bringup'), 'launch', 'robot.launch.py')


def _expected_links_and_roots():
    """Parse the installed robot_description; return (link set, root list).

    The description's own gate expands the installed xacro with the xacro CLI
    and parses the result (D27); this test does the same to know what TF
    frames the bringup should publish -- derived, never hand-typed.
    """
    from urdf_parser_py.urdf import URDF
    xacro = _install_share('robot_description') + '/urdf/robot.urdf.xacro'
    expanded = subprocess.run(
        [_require_tool('xacro'), xacro], capture_output=True, text=True)
    assert expanded.returncode == 0, (
        'xacro failed to expand the installed description:\n' + expanded.stderr)
    # PR8b: ros2_control <transmission> blocks are not parseable by
    # urdf_parser_py (it only duck-types new_transmission/pr2_transmission);
    # strip them for the frame derivation. Transmissions are validated by
    # ros2_control's runtime parser (R-PR8b-7).
    import re
    xml = re.sub(r'<transmission\b[^>]*>.*?</transmission>',
                 '', expanded.stdout, flags=re.S)
    robot = URDF.from_xml_string(xml)
    child_frames = {j.child for j in robot.joints}
    links = {link.name for link in robot.links}
    roots = sorted(links - child_frames)
    return links, roots


def test_launch_file_is_installed_and_generates():
    """The shipped launch file exists and its description builds.

    Imports the installed launch module and calls generate_launch_description,
    which is the cheapest check that the launch *loads* (acceptance criterion)
    without needing the ROS graph. The heavy verification -- that it actually
    runs and publishes the right tree -- is test_bringup_publishes_* below.
    """
    import importlib.util
    from launch_ros.actions import Node
    path = _launch_path()
    assert os.path.isfile(path), (
        'launch file not present in the installed tree: %s' % path)
    spec = importlib.util.spec_from_file_location('robot_bringup_launch', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    description = module.generate_launch_description()
    # It must contain the two nodes the bringup promises.
    seen = []
    for action in description.entities:
        if isinstance(action, Node):
            seen.append((getattr(action, 'node_package', '?'),
                         getattr(action, 'node_executable', '?')))
    flat = ' '.join('%s/%s' % n for n in seen)
    assert 'robot_state_publisher/robot_state_publisher' in flat
    assert 'joint_state_publisher/joint_state_publisher' in flat


def _spawn_launch(env):
    """Start the real bringup launch as a subprocess (process-group teardown)."""
    process = subprocess.Popen(
        [_require_tool('ros2'), 'launch', 'robot_bringup', 'robot.launch.py'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env=env, start_new_session=True)
    group = os.getpgid(process.pid)
    output = []
    reader = threading.Thread(
        target=lambda: output.append(process.stdout.read()), daemon=True)
    reader.start()
    return process, group, output, reader


def _terminate_group(process, group):
    """Take down the launch and everything it spawned (D29 lesson)."""
    import signal
    try:
        os.killpg(group, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


def _parse_frame_string(text):
    """Parse RSP's all_frames_as_string into {child: parent}."""
    parents = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith('Frame '):
            continue
        head = line[len('Frame '):]
        child, _, rest = head.partition(' exists with parent ')
        parent = rest.rstrip('.')
        parents[child] = parent
    return parents


def _collect_tf(domain, timeout_s, deadline_exit):
    """Return {child: parent} of all published frames (not only static)."""
    import rclpy
    from rclpy.node import Node
    from tf2_ros import Buffer, TransformListener

    os.environ['ROS_DOMAIN_ID'] = domain
    rclpy.init()
    node = Node('tf_tree_probe')
    buffer = Buffer()
    # The TransformListener populates the buffer from /tf in a background
    # thread; keep a strong reference on the node so it is never collected
    # before the test finishes reading.
    node._tf_listener = TransformListener(buffer, node)
    executor = rclpy.executors.SingleThreadedExecutor()
    executor.add_node(node)
    spin = threading.Thread(target=executor.spin, daemon=True)
    spin.start()
    try:
        deadline = time.monotonic() + timeout_s
        parents = {}
        while time.monotonic() < deadline:
            if deadline_exit.is_set():
                break
            try:
                parents = _parse_frame_string(buffer.all_frames_as_string())
            except Exception:
                parents = {}
            # Stop once the tree is substantially populated (bootstrap).
            if 'base_link' in set(parents.values()) and len(parents) >= 20:
                break
            time.sleep(0.1)
        return parents
    finally:
        executor.shutdown()
        node.destroy_node()
        # rclpy.shutdown() is deferred: spinning it down mid-thread aborts with
        # "terminate called without an active exception" under this pytest host.
        # The process exits immediately after the test, so the cost is nil.


def test_bringup_publishes_complete_rooted_tf():
    """The bringup launch runs and RSP publishes the full, correct tree.

    The expected link set is from the installed URDF, so this gate holds the
    *published* tree against the *declared* model: every link present, the
    root is base_link, every frame reached through in-graph parents (the tree
    connects -- no orphans), and D29's odometry note is respected
    (base_footprint is a child of base_link, never the root).
    """
    expected_links, expected_roots = _expected_links_and_roots()
    assert expected_roots == [EXPECTED_ROOT], (
        'installed description should be rooted at %s, got %s'
        % (EXPECTED_ROOT, expected_roots))

    env = dict(os.environ, ROS_DOMAIN_ID=TF_DOMAIN_ID)
    process, group, output, reader = _spawn_launch(env)
    deadline_exit = threading.Event()
    try:
        parents = _collect_tf(TF_DOMAIN_ID, TF_READ_TIMEOUT_S, deadline_exit)

        if not parents:
            logs = ''.join(output)
            raise AssertionError(
                'no TF frames collected within %.0fs. Bringup log:\n%s'
                % (TF_READ_TIMEOUT_S, logs or '<no output>'))

        children = set(parents)
        referenced_parents = set(parents.values())
        published = children | referenced_parents

        # Completeness: every URDF link published, nothing extra.
        missing = sorted(expected_links - published)
        assert not missing, (
            'bringup did not publish URDF link(s): %s' % missing)
        extra = sorted(published - expected_links)
        assert not extra, (
            'bringup published unexpected frame(s): %s' % extra)

        # Rooting: the only root is base_link.
        roots = sorted(published - children)
        assert roots == [EXPECTED_ROOT], (
            'TF root should be exactly base_link, got %r' % roots)

        # Connectivity: every published parent is itself in the tree.
        dangling = sorted(referenced_parents - published)
        assert not dangling, (
            'published frames referencing absent parents: %s' % dangling)

        # D29 odometry note: base_footprint is a CHILD of base_link, never root.
        assert BASE_FOOTPRINT in parents, (
            '%r missing from the published tree' % BASE_FOOTPRINT)
        assert parents[BASE_FOOTPRINT] == EXPECTED_ROOT, (
            'D29: %s must be a direct child of %s, got %r'
            % (BASE_FOOTPRINT, EXPECTED_ROOT, parents[BASE_FOOTPRINT]))
        assert BASE_FOOTPRINT not in roots, (
            'D29: %s must not be the TF root' % BASE_FOOTPRINT)
    finally:
        deadline_exit.set()
        _terminate_group(process, group)
