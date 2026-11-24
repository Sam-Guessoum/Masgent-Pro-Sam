# Masgent React workspace

The React workspace browses materials databases, prepares crystal structures and VASP inputs, and runs ML potential simulations through the Python job manager. The Results Library shows run status, plots, structures and downloadable files.

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

The default container includes CHGNet and CPU PyTorch. To run CHGNet locally, install CPU PyTorch with `python -m pip install torch --index-url https://download.pytorch.org/whl/cpu`, then install the model with `python -m pip install -r web/requirements-mlp.txt`. A container built with `--build-arg INSTALL_CHGNET=0` omits the engine. Other engines require their corresponding Python packages and model weights.

To verify the setup, open **ML potentials**, choose the bundled **fcc Al (mp-134)** structure, select **CHGNet** and **Geometry relaxation**, and run the job. The Results Library shows the energy, optimization history, and the relaxed crystal. The same tab supports equation of state, elastic constants, and molecular dynamics.
