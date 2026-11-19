# Masgent React workspace

This first migration covers file format conversion, POSCAR coordinate conversion, supercells, vacancies, substitutions, interstitials, surface slabs and KPOINTS generation from bundled POSCAR examples. The results library shows run status and downloads. The production web app runs these tools using the existing Python job manager.

The Streamlit application still provides uploads, the AI assistant, ML tools and potentials, advanced VASP workflows, interfaces, SQS, project management and 3D visualization.

## Run locally

From the repository root, install `web/requirements.txt` in a Python 3.12 environment and build the frontend with `npm --prefix web ci` and `npm --prefix web run build`.

Set these environment variables before starting the server.

| Variable | Value |
| --- | --- |
| `MASGENT_WEB_PASSWORD` | Random shared password of at least 20 characters |
| `MASGENT_WEB_SESSION_KEY` | Separate random secret of at least 32 characters |
| `MASGENT_WEB_LOCAL_HTTP` | `1` for local HTTP development only |

Run `gunicorn --bind 127.0.0.1:8080 --workers 1 --threads 4 'web.server:create_app()'` and open http://127.0.0.1:8080.

## Host the workspace

Build from the repository root with `docker build -f web/Dockerfile -t masgent-web .`. Deploy the container to an HTTPS host with one replica, leaving `MASGENT_WEB_LOCAL_HTTP` unset. Configure the two secrets on the host and mount a writable persistent volume at `/data` for container UID 10001. The app listens on port 8080. Anyone with the password shares the same workspace and run history.

The GitHub repository stores the source. An HTTPS container host runs the server, authenticates requests and executes the simulation workers.

## Materials Project and ML potentials

Set `MP_API_KEY` as a host secret to enable the authenticated Materials Project catalog. Browse by formula, material ID, or periodic-table elements. Result pages fetch crystal coordinates on demand.

Use `docker build --build-arg INSTALL_CHGNET=1 -f web/Dockerfile -t masgent-web-mlp .` to add CHGNet and CPU PyTorch. The run form offers the engines installed on the host. Other engines require their corresponding Python packages and model weights. Validate memory and runtime limits before enabling them on a shared host. PNG plots produced by successful runs can be viewed in Results Library.
