"""Structure helpers: detection, summaries and the 3Dmol.js viewer HTML."""
from __future__ import annotations

import html
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

STRUCTURE_NAMES = ("POSCAR", "CONTCAR")
STRUCTURE_SUFFIXES = (".vasp", ".cif", ".xyz", ".poscar", ".extxyz")
VIEWER_JS = "https://cdnjs.cloudflare.com/ajax/libs/3Dmol/2.4.2/3Dmol-min.js"

# Jmol colours (subset) used by the viewer legend.
JMOL = {
    "H": "#FFFFFF", "Li": "#CC80FF", "C": "#909090", "N": "#3050F8", "O": "#FF0D0D", "F": "#90E050", "Na": "#AB5CF2",
    "Mg": "#8AFF00", "Al": "#BFA6A6", "Si": "#F0C8A0", "P": "#FF8000", "S": "#FFFF30", "Cl": "#1FF01F", "K": "#8F40D4",
    "Ca": "#3DFF00", "Sc": "#E6E6E6", "Ti": "#BFC2C7", "V": "#A6A6AB", "Cr": "#8A99C7", "Mn": "#9C7AC7", "Fe": "#E06633",
    "Co": "#F090A0", "Ni": "#50D050", "Cu": "#C88033", "Zn": "#7D80B0", "Ga": "#C28F8F", "Ge": "#668F8F", "As": "#BD80E3",
    "Y": "#94FFFF", "Zr": "#94E0E0", "Nb": "#73C2C9", "Mo": "#54B5B5", "Ag": "#C0C0C0", "Sn": "#668080", "La": "#70D4FF",
    "Ce": "#FFFFC7", "Hf": "#4DC2FF", "Ta": "#4DA6FF", "W": "#2194D6", "Pt": "#D0D0E0", "Au": "#FFD123", "Pb": "#575961",
}


def looks_like_structure(path: Path) -> bool:
    p = Path(path)
    name = p.name
    if name.endswith(".spec") or name.startswith(("POTCAR", "INCAR", "KPOINTS")):
        return False
    return name.startswith(STRUCTURE_NAMES) or p.suffix.lower() in STRUCTURE_SUFFIXES


def _read_any(path: Path):
    """Read with pymatgen, falling back to ASE (handles xyz/extxyz/cif/vasp)."""
    from pymatgen.core import Structure

    try:
        return Structure.from_file(str(path))
    except Exception:
        from ase.io import read
        from pymatgen.io.ase import AseAtomsAdaptor

        atoms = read(str(path))
        if not atoms.cell.any():
            atoms.center(vacuum=5.0)
        return AseAtomsAdaptor.get_structure(atoms)


@lru_cache(maxsize=256)
def _summary_cached(path: str, mtime: float) -> dict[str, Any]:
    s = _read_any(Path(path))
    out: dict[str, Any] = {
        "formula": s.composition.reduced_formula,
        "full_formula": s.composition.formula.replace(" ", ""),
        "natoms": len(s),
        "elements": sorted({str(e) for e in s.composition.elements}),
        "counts": {str(k): int(v) for k, v in s.composition.get_el_amt_dict().items()},
        "a": s.lattice.a, "b": s.lattice.b, "c": s.lattice.c,
        "alpha": s.lattice.alpha, "beta": s.lattice.beta, "gamma": s.lattice.gamma,
        "volume": s.volume, "density": float(s.density),
    }
    try:
        from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

        sga = SpacegroupAnalyzer(s, symprec=0.1)
        out["spacegroup"] = f"{sga.get_space_group_symbol()} ({sga.get_space_group_number()})"
        out["crystal_system"] = sga.get_crystal_system()
    except Exception:
        out["spacegroup"] = "n/a"
    return out


def summary(path: Path | str) -> dict[str, Any]:
    p = Path(path)
    return _summary_cached(str(p.resolve()), p.stat().st_mtime)


def elements_in(path: Path | str) -> list[str]:
    try:
        return summary(path)["elements"]
    except Exception:
        return []


def to_xyz_frame(structure) -> str:  # noqa: ANN001
    lines = [str(len(structure)), "frame"]
    for site in structure:
        x, y, z = site.coords
        lines.append(f"{site.specie.symbol if hasattr(site.specie, 'symbol') else site.specie} {x:.6f} {y:.6f} {z:.6f}")
    return "\n".join(lines)


def viewer_html(path: Path | str | None = None, frames_xyz: list[str] | None = None, lattice: list[list[float]] | None = None,
                height: int = 420, style: str = "ball-and-stick", show_cell: bool = True, supercell: tuple[int, int, int] = (1, 1, 1),
                dark: bool = False, labels: bool = False, animate: bool = False) -> str:
    """Self-contained HTML (3Dmol.js) for a structure or an XYZ trajectory.

    Atom data is JSON-encoded (never interpolated as markup), so names/content
    from uploaded files cannot inject script.
    """
    if path is not None:
        s = _read_any(Path(path))
        if supercell != (1, 1, 1):
            s = s.copy()
            s.make_supercell(list(supercell))
        frames_xyz = [to_xyz_frame(s)]
        lattice = s.lattice.matrix.tolist()
    data = json.dumps({"frames": frames_xyz or [], "lattice": lattice, "style": style, "cell": show_cell,
                       "labels": labels, "animate": animate, "colors": JMOL})
    data = data.replace("</", "<\\/")
    bg = "#0e1117" if dark else "#ffffff"
    fg = "#e6e6e6" if dark else "#333333"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<script src="{VIEWER_JS}"></script>
<style>html,body{{margin:0;padding:0;background:{bg};font-family:system-ui,sans-serif;color:{fg}}}
#v{{width:100%;height:{int(height)}px;position:relative}}
#bar{{position:absolute;z-index:10;top:6px;right:6px;display:flex;gap:4px}}
#bar button{{font:12px system-ui;padding:3px 7px;border-radius:6px;border:1px solid #8884;background:{bg};color:{fg};cursor:pointer}}
#leg{{position:absolute;z-index:10;bottom:6px;left:8px;font-size:12px;display:flex;gap:8px;flex-wrap:wrap}}
#leg span i{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:3px;border:1px solid #8886}}
</style></head><body><div id="v"><div id="bar">
<button onclick="toggleStyle()" title="Toggle ball-and-stick / spacefill">Style</button>
<button onclick="toggleCell()" title="Show or hide the unit cell">Cell</button>
<button onclick="toggleSpin()" title="Spin">Spin</button>
<button onclick="snap()" title="Save a PNG screenshot">PNG</button>
<button onclick="viewer.zoomTo();viewer.render()" title="Reset view">Reset</button>
</div><div id="leg"></div></div>
<script>
const D = {data};
let style = D.style, showCell = D.cell, spinning = false;
const viewer = $3Dmol.createViewer(document.getElementById('v'), {{backgroundColor: '{bg}'}});
function styleSpec() {{
  return style === 'spacefill' ? {{sphere: {{scale: 0.9, colorscheme: 'Jmol'}}}}
       : style === 'stick' ? {{stick: {{radius: 0.15, colorscheme: 'Jmol'}}}}
       : {{sphere: {{scale: 0.3, colorscheme: 'Jmol'}}, stick: {{radius: 0.12, colorscheme: 'Jmol'}}}};
}}
function draw() {{
  viewer.clear();
  let m;
  if (D.frames.length > 1) {{ m = viewer.addModelsAsFrames(D.frames.join('\\n'), 'xyz'); }}
  else {{ m = viewer.addModel(D.frames[0] || '', 'xyz'); }}
  viewer.setStyle({{}}, styleSpec());
  if (D.labels) {{ viewer.addPropertyLabels('elem', {{}}, {{fontSize: 10, showBackground: false}}); }}
  if (showCell && D.lattice) {{
    const L = D.lattice, o = [0,0,0];
    const add = (a,b)=>[a[0]+b[0],a[1]+b[1],a[2]+b[2]];
    const c = [o, L[0], L[1], L[2], add(L[0],L[1]), add(L[0],L[2]), add(L[1],L[2]), add(add(L[0],L[1]),L[2])];
    const e = [[0,1],[0,2],[0,3],[1,4],[1,5],[2,4],[2,6],[3,5],[3,6],[4,7],[5,7],[6,7]];
    e.forEach(([i,j]) => viewer.addCylinder({{start:{{x:c[i][0],y:c[i][1],z:c[i][2]}}, end:{{x:c[j][0],y:c[j][1],z:c[j][2]}}, radius:0.03, color:'#888', fromCap:1, toCap:1}}));
  }}
  viewer.zoomTo();
  if (D.frames.length > 1 && D.animate) {{ viewer.animate({{loop: 'forward', interval: 120}}); }}
  viewer.render();
}}
function toggleStyle() {{ style = style === 'ball-and-stick' ? 'spacefill' : style === 'spacefill' ? 'stick' : 'ball-and-stick'; draw(); }}
function toggleCell() {{ showCell = !showCell; draw(); }}
function toggleSpin() {{ spinning = !spinning; viewer.spin(spinning ? 'y' : false); }}
function snap() {{ const a = document.createElement('a'); a.href = viewer.pngURI(); a.download = 'structure.png'; a.click(); }}
const els = new Set(); (D.frames[0]||'').split('\\n').slice(2).forEach(l => {{ const s = l.trim().split(/\\s+/)[0]; if (s) els.add(s); }});
document.getElementById('leg').innerHTML = [...els].map(e => `<span><i style="background:${{D.colors[e]||'#ff1493'}}"></i>${{e.replace(/[^A-Za-z]/g,'')}}</span>`).join('');
draw();
</script></body></html>"""


def trajectory_frames(traj_path: Path | str, max_frames: int = 200) -> tuple[list[str], list[list[float]] | None, int]:
    """Read an ASE .traj/XDATCAR and return XYZ frames (subsampled) for animation."""
    from ase.io import read

    images = read(str(traj_path), index=":")
    n = len(images)
    step = max(1, n // max_frames)
    frames = []
    for at in images[::step]:
        lines = [str(len(at)), "frame"] + [f"{a.symbol} {a.position[0]:.5f} {a.position[1]:.5f} {a.position[2]:.5f}" for a in at]
        frames.append("\n".join(lines))
    lat = images[0].cell.array.tolist() if n else None
    return frames, lat, n


def escape(s: str) -> str:
    return html.escape(str(s))
