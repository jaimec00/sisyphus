# meshes

Collision and visual geometry for the robot description, installed to
`share/robot_description/meshes/`.

Empty for now by design: PR1 (#61) ships the package, the install layout and
the expand/parse gate; geometry arrives with the base (PR2), column (PR3) and
arms (PR4), cribbed from the LeRobot/XLeRobot lineage per D26.

This file is not a placeholder to delete — it is what makes the directory
exist. `setup.py` installs `glob('meshes/*')`, and `glob()` skips dotfiles, so
a `.gitkeep` would leave the installed directory missing and the gate's
layout assertion red.
