# Manual test checklist (about 20 minutes)

Start the app from the repo root: `.venv\Scripts\python.exe -m streamlit run streamlit_app\app.py`. Tick each box as you go. Times are for a CPU-only laptop.

## A. First start and setup (3 min)
- [ ] The **Welcome** dialog appears with a 3-step tour. Click **Run the sample project**.
- [ ] Home → *Recent runs* shows "Sample: Al 2×2×2 supercell" turn *Succeeded*. Within about a minute, "Sample: CHGNet single point on Al supercell" appears and also succeeds.
- [ ] Sidebar → **Settings** → paste your MP key → **Save** → **Test connection** shows "Connected: fetched mp-134 (Al)". The key is shown only as `••••••••abcd`.
- [ ] Sidebar → **Setup doctor**: Python, Masgent and the packages are green. The GPU shows as an orange note (CPU build) with the exact install command. "torch installation integrity" is flagged (E02).

## B. Build a structure (4 min)
- [ ] Sidebar search box: type `supercell`, press Enter. It opens *Build structure → Supercell*.
- [ ] **Load example → 2×2×2 supercell of fcc Al**. The form fills in and shows "Inputs are valid".
- [ ] Change a matrix cell to `0` on the diagonal. A red "singular" note appears and **Run** is disabled. Restore it.
- [ ] **Run**. The **Results** panel shows *Running* with a live log, then *Succeeded* with the message.
- [ ] **Files** tab → *Structures*: the 3D viewer shows 32 Al atoms. Toggle *Style* / *Unit cell* / *Repeat 2*, and **PNG** downloads a screenshot.
- [ ] **Use in… → Vacancies** opens *Defects & alloys* with the supercell pre-selected. Run it with element Al, count 1, and get a 31-atom POSCAR.
- [ ] *Build structure → Materials Project*: search `NaCl`. The table lists polymorphs sorted by energy above hull. Select a row to preview in 3D, then **Use this structure**.

## C. ML potential with live results (4 min)
- [ ] *ML potentials*: the four model cards show *Installed*.
- [ ] **Load example → CHGNet MD 300 steps @ 600 K**, then **Run**. During the run, *Progress & metrics* shows the temperature/energy plot growing and a progress bar.
- [ ] Start a second MD run and press **Cancel** after a few seconds. The status becomes *Cancelled* and partial files are kept.
- [ ] When the first run finishes: the metrics (mean T, drift) and the T/E/Ekin plots show. **Animate trajectory** plays the MD frames.
- [ ] Try **CHGNet EOS**: an E–V curve with a Birch–Murnaghan fit, V₀, B₀ (≈ 70–80 GPa for Al) and B₀′.

## D. DFT workflows and VASP inputs (3 min)
- [ ] *VASP inputs → Full VASP input set → Load example → Relax fcc Al*, then Run. The files include `POTCAR.spec` (not POTCAR). Opening it shows the `cat …` command. **Download all (ZIP)** works.
- [ ] *DFT workflows → EOS → Analyze → Load example → Masgent example*, then Run. You get an interactive E–V plot with B₀, and `POSCAR_equilibrium` appears under Files.
- [ ] *Elastic → Analyze → example*: a 6×6 heatmap, K/G/E/ν, and a mechanical-stability note.
- [ ] *NEB → Analyze → example*: the energy profile and barrier.

## E. Results library: reopen after restart (3 min)
- [ ] Stop the app (`Ctrl+C`) and start it again.
- [ ] **Results library** lists every run above. Filter by tool = *Supercell*, status = *succeeded*, then clear the filters.
- [ ] Open a run, set a label and tags, star it, add a note, **Save**. Search for the label and it is found.
- [ ] Select two CHGNet runs: the **Compare** view shows the parameter diff, key results and overlaid plots.
- [ ] **HTML report** downloads a one-page report (Print → Save as PDF works in the browser).
- [ ] **Delete** → confirm → the run disappears. **Show trash** → **Restore** brings it back.
- [ ] **Re-run with these parameters** (Outputs tab) opens the tool with the same inputs.

## F. AI assistant: Plan, Confirm, Execute (3 min)
- [ ] *AI assistant*: choose a provider (Masgent AI needs no key; the first reply may take a minute).
- [ ] Ask "Generate a 2×2×2 supercell of the current structure and relax it with CHGNet". The assistant replies with a **plan** and a card with **Approve & execute / Edit parameters / Cancel plan**.
- [ ] **Edit parameters** → "use MatterSim instead" gives a revised plan. **Approve & execute** runs the steps, each shown with a status and an **Open run** link to the library.
- [ ] Type something that should not run without approval. Nothing appears in the library until you approve.

## G. Sanity (1 min)
- [ ] Open `streamlit_app\workspace\app.log` and any run's `logs.txt`: your API key does not appear (search for its last 6 characters).
- [ ] Settings → toggle dark theme (⋮ menu → Settings): the pages, plots and 3D viewer stay readable.
