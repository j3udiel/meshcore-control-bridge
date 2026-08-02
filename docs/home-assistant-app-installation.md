# Home Assistant App Installation

## Repository URL

The intended user-facing installation path is the normal Home Assistant App
repository flow:

1. Open Home Assistant.
2. Go to Settings -> Apps -> Repositories.
3. Select Add.
4. Enter:

   ```text
   https://github.com/j3udiel/meshcore-control-bridge
   ```

5. Save.
6. Open the App Store.
7. Install `MeshCore Control Bridge`.

Home Assistant reads the repository default branch. The App will not appear from
this URL until `repository.yaml` and `meshcore-control-bridge/config.yaml` are on
that default branch.

## Current Test Strategy

The current branch prepares the main project repository to act as the App
repository once the Home Assistant transport and App PRs are merged. This avoids
manual code duplication and keeps the App package generated from the Python
source tree.

Do not merge the experimental USB transport for this installation path. The App
uses the Home Assistant MeshCore integration transport and `SUPERVISOR_TOKEN`.

## GHCR Image

`meshcore-control-bridge/config.yaml` references the generic multi-architecture
image:

```text
ghcr.io/j3udiel/meshcore-control-bridge
```

The App version selects the image tag. Version `0.1.0` means:

```text
ghcr.io/j3udiel/meshcore-control-bridge:0.1.0
```

The image publication workflow is manual and tag-driven. It does not publish
from pull requests.

## Optional Separate App Repository

A future dedicated repository such as `j3udiel/home-assistant-apps` can contain
only:

```text
repository.yaml
meshcore-control-bridge/
  config.yaml
  Dockerfile
  run.sh
  README.md
  DOCS.md
  CHANGELOG.md
  apparmor.txt
  translations/
```

If that repository is created, the App should still pull the same GHCR image and
the Python package should be synchronized by automation, not by manual copying.
