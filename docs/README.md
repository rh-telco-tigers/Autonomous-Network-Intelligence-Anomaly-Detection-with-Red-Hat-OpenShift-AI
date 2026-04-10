# Documentation

This repo now separates operator-facing installation docs from the deeper architecture references.

## Start Here

1. [Installation guide](./installation/README.md)
2. [Platform overview](./installation/01-platform-overview.md)
3. [Installation](./installation/02-installation.md)
4. [Validation](./installation/03-validation.md)
5. [Troubleshooting](./installation/04-troubleshooting.md)

## Architecture References

- [Architecture by phase](./architecture/README.md)
- [Engineering specification](./architecture/engineering-spec.md)
- [Feature store training path](./architecture/feature-store-training-path.md)

## Demo Access Notes

- Gitea user: `gitadmin`
- Gitea password: `GiteaAdmin123!`
- API admin token: `demo-token`
- API operator token: `demo-operator-token`
- API viewer token: `demo-viewer-token`
- Plane user: `plane-admin@ani-demo.local`
- Plane password: `plane`
- OpenIMSs WebUI user: `admin`
- OpenIMSs WebUI password: `1423`
- MinIO console user: `minioadmin`
- MinIO console password: `minioadmin`

## Notes

- The platform is installed through GitOps from the in-cluster Gitea repository.
- AAP and EDA are enabled by default in the GitOps runtime config; they become live after the AAP license is imported and the controller bootstrap completes.
- Plane is optional, but when enabled the bootstrap job creates the demo admin user, workspace, project, and integration secret automatically.
