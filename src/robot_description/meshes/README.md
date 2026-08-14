# meshes

Collision and visual geometry for the robot description, installed to
`share/robot_description/meshes/`.

Still empty by design, and it is not the base's doing: PR2 authored the
3-omniwheel base from primitives rather than vendoring LeKiwi's meshes (the
upstream omniwheel STL is 15 MB and referenced three times; convex primitives
are the better collision geometry for a mobile base anyway) — see **D29**. The
first real mesh set is therefore expected with the arms (PR4), cribbed from
the LeRobot/XLeRobot lineage per D26, and that is also the PR that owes the
`os.walk` install rewrite D27 deferred.

This file is not a placeholder to delete — it is what makes the directory
exist. `setup.py` installs `glob('meshes/*')`, and `glob()` skips dotfiles, so
a `.gitkeep` would leave the installed directory missing and the gate's
layout assertion red.
