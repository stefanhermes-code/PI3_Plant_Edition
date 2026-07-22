"""Single source of truth for the app version shown in the navigation bar.

Convention: bump this on every commit that gets pushed to GitHub.
- Patch (x.y.Z) for fixes, small tweaks, content/data changes.
- Minor (x.Y.0) for new features/pages/schema additions.
- Major (X.0.0) reserved for breaking changes to the data model or workflow.

1.0.0 marks the first version-tracked release (the production-run-centric
rework + machine setup). Everything before this was unversioned.
"""

APP_VERSION = "1.6.2"
