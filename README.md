# Undergraduate Research Plan: Scintillator Light Uniformity Study with Geant4

**Duration:** 4 Weeks  
**Goal:** Study light collection uniformity in plastic scintillator tiles of varying sizes/thicknesses using Geant4 simulations.

---

## Week 1: Environment Setup & Geant4 Basics

### 1.1 Building the Custom Docker Environment
We use Docker to ensure a consistent environment across different operating systems. Since you are likely on a Mac with Apple Silicon (M1/M2/M3), we need to build a native **arm64** image for best performance.

**Task:**
1. Open your terminal and navigate to the project root.
2. Build the image:
   ```bash
   docker build -t geant4-dev:latest .
   ```
   *Explanation:* This reads the `Dockerfile`, installs Ubuntu 24.04, and compiles Geant4 11.3.2 with multi-threading and visualization support.
3. Start the container:
   ```bash
   docker run -it --name g4-work -v $(pwd):/work geant4-dev:latest
   ```

### 1.2 Introduction to Geant4 Concepts
- **The World:** Every simulation happens inside a "World" volume.
- **Materials:** We define physical properties (refractive index, absorption length).
- **Volumes:** `G4Box` (Shape) -> `G4LogicalVolume` (Material) -> `G4VPhysicalVolume` (Placement).

### 1.3 Running your First Example
We will use `OpNovice2` as our base. It is designed to test optical processes.
1. Inside the container:
   ```bash
   cd /work/test/OpNovice2/build
   cmake ..
   make -j$(nproc)
   ./OpNovice2 electron.mac
   ```
2. Observe the output. It should generate an `opnovice2.root` file containing histograms of optical photons.

---

## Week 2: Visualization & Analysis

### 2.1 Visualizing Events
You need to "see" the photons to debug your geometry.
- **Interactive Mode:** Run `./OpNovice2` without arguments. If your X11/Quartz is set up, a GUI will open.
- **Offscreen (PNG):** Use the provided `vis-png.mac`.
  ```bash
  ./OpNovice2 vis-png.mac
  ```
  This will generate `.png` files showing the geometry and particle tracks.
- **HepRep:** A powerful way to explore 3D events. Add `/vis/open HepRepFile` to a macro to generate `.heprep` files, which can be viewed in the [HepRApp](https://www.slac.stanford.edu/~perl/HepRApp/) viewer.

### 2.2 Analyzing Example Macros
Run and compare these macros in `test/OpNovice2/`:
- `boundary.mac`: Focuses on surface reflections.
- `scint_by_particle.mac`: Shows how different particles produce different amounts of light.
- **Exercise:** Look at `HistoManager.cc` to see which histogram ID corresponds to what data (e.g., ID 2 is the scintillation spectrum).

---

## Week 3: Designing the Scintillator Tile

### 3.1 Modifying the Geometry
Open `src/DetectorConstruction.cc`. Currently, it creates a "Tank" (a large cube).
**Tasks:**
1. **Reshape:** Change the `Tank` dimensions to represent a tile (e.g., $10cm \times 10cm \times 0.5cm$).
2. **Material:** Change the material from `G4_WATER` to a plastic scintillator. You can use NIST materials like `G4_PLASTIC_SC_VINYLTOLUENE`.
3. **Optical Properties:** In Geant4, you must explicitly set the `SCINTILLATIONYIELD` and `RINDEX` for the material in the `MaterialPropertiesTable`.

### 3.2 Defining the Light Source
Use the `/gun/` commands in a macro to "shoot" particles (like muons or electrons) into the tile to generate scintillation light.
- **Goal:** Ensure photons are being generated and reflecting off the internal surfaces (Total Internal Reflection).

---

## Week 4: SiPM Integration & Uniformity Study

### 4.1 Simulating a SiPM
A SiPM (Silicon Photomultiplier) can be modeled as a small volume (e.g., $3mm \times 3mm \times 1mm$) attached to the scintillator.
- **Implementation:**
    1. Define a `SiPM_Box`.
    2. Place it on the surface of the tile using `G4PVPlacement`.
    3. To count photons, you can check in `SteppingAction.cc` if a photon enters the `SiPM` volume and then "kill" the track to simulate absorption/detection.

### 4.2 Testing SiPM Location (The Research Goal)
Can we move the SiPM in Geant4? **Yes.**
- **Manual Method:** Change the `G4ThreeVector` position in `DetectorConstruction.cc` and recompile.
- **Advanced Method:** Use a "Messenger" class (see `DetectorMessenger.cc`) to allow changing the position via a macro command like `/mycdet/sipm/pos 10 10 0 mm`.

### 4.3 The Uniformity Study
1. Fix the SiPM at one corner.
2. Use a macro to shoot a particle beam at different grid points $(x,y)$ on the tile.
3. Record the number of detected photons for each point.
4. **Analysis:** Plot "Detection Efficiency vs. Distance from SiPM."
5. **Comparison:** Repeat for different tile thicknesses (0.2cm vs 0.5cm vs 1cm).

---

## Final Deliverable
A report containing:
1. PNG images of your tile and SiPM geometry.
2. A plot showing the number of detected photons as a function of the source position.
3. A recommendation on the optimal SiPM placement for the best light uniformity.


# Extended Research Plan: Detailed Light Collection & Uniformity Studies (Weeks 5–12)

**Prerequisite:** Completion of Weeks 1–4 (working Geant4 environment, modified tile geometry, SiPM volume with photon counting in `SteppingAction.cc`, and a basic uniformity scan).

**Overall Goal:** Build a quantitative, experimentally-validated model of light collection in plastic scintillator tiles read out by a SiPM. Understand how collection efficiency and its *uniformity* depend on hit position, tile thickness, surface finish, the presence of a dimple, and beam energy/type. Ultimately, recommend a tile + SiPM configuration that maximizes both total light yield and position-independence of the response.

A guiding principle for all of these weeks: **change one variable at a time, keep everything else fixed, and always save the macro and a summary of the output so runs are reproducible.** Each study below should be driven by batch (headless) runs writing histograms/ntuples, with visualization reserved for sanity checks.

---

## Week 5: Building a Robust Position-Scan Framework

Before adding physics complexity, invest in infrastructure. The uniformity study from Week 4 needs to become a repeatable, scriptable scan rather than a hand-edited macro.

### 5.1 Parameterize the Beam Position via the Messenger
You already added (or will add) a `DetectorMessenger`. Add an analogous messenger command for the *primary generator* so the beam position can be set from a macro without recompiling. The cleanest approach is to use the General Particle Source (GPS) instead of the simple particle gun, since GPS exposes position, angular distribution, and energy spectrum entirely through macro commands.

- Switch `PrimaryGeneratorAction.cc` to use `G4GeneralParticleSource` instead of `G4ParticleGun`.
- Confirm you can set the position from a macro:
  ```
  /gps/particle e-
  /gps/pos/type Point
  /gps/pos/centre 0. 0. 2.5 mm
  /gps/direction 0 0 -1
  /gps/energy 1.0 MeV
  ```
> [!NOTE]
> Completed, 6/15 12:28.

### 5.2 Scriptable Grid Scans
Write a small shell or Python script *outside* the container's run loop that generates one macro per grid point, or use Geant4's macro looping:
```
/control/loop scan.mac x -45 45 5
```
where `scan.mac` uses an aliased variable `{x}` in the `/gps/pos/centre` command. Decide on a grid (e.g., 5 mm spacing over a 100 mm × 100 mm tile gives a 21 × 21 = 441-point map).

### 5.3 Output Bookkeeping
For each run, record at minimum: hit position (x, y), number of generated optical photons, number of photons reaching the SiPM, and detection efficiency (detected / generated). Write these to an ntuple via `G4AnalysisManager` so each row is one event and you can post-process freely. Avoid writing only pre-binned histograms at this stage — you lose flexibility.

**Deliverable:** A single command that produces a 2-D efficiency map over the tile face, saved to a `.root` (or `.csv`) file.

---

## Week 6: Tile Thickness Dependence

### 6.1 Parameterize Thickness
Add a messenger command (e.g., `/mycdet/tile/thickness 5 mm`) so thickness can be set without recompiling, or — if that is too invasive — define a build-time set of thickness values and script the recompile/run cycle. The messenger route is strongly preferred for the studies ahead.

### 6.2 The Study
For each thickness in {2 mm, 5 mm, 10 mm} (extend as desired):
1. Re-run the full position scan from Week 5.
2. Compute the mean detection efficiency and its spatial RMS (a proxy for *non*-uniformity).
3. Track total light yield separately from uniformity — thicker tiles generate more light per traversal but may collect it less uniformly.

### 6.3 Expected Physics & Discussion
- Thicker tiles produce more scintillation photons per minimum-ionizing particle (more path length), raising raw yield.
- More material also means more self-absorption and more bounces before reaching the SiPM, which can degrade uniformity.
- **Plot:** Mean efficiency and efficiency-RMS vs. thickness on the same axes. Identify any trade-off between yield and uniformity.

---

## Week 7: Surface Finish & Polish Conditions

This is where optical surface modeling becomes central, and it is the part most students get subtly wrong, so go slowly.

### 7.1 Understanding Geant4 Optical Surfaces
Light collection in a tile is dominated by what happens at the boundaries. In Geant4, you control this with `G4OpticalSurface`, attached either as a `G4LogicalSkinSurface` (one surface wrapping a whole volume) or a `G4LogicalBorderSurface` (between two specific volumes, direction-dependent).

Key parameters to understand and document in your own words:
- **Model:** `unified` (recommended, fine-grained control) vs. `glisur`.
- **Finish:** `polished`, `ground`, `polishedfrontpainted`, `groundfrontpainted`, etc.
- **`sigma_alpha`:** in the unified model, the standard deviation (radians) of the micro-facet normal distribution. Small `sigma_alpha` ≈ near-polished; larger ≈ rougher/more diffuse.
- **Reflectivity** and the four reflection components (`SPECULARLOBE`, `SPECULARSPIKE`, `BACKSCATTER`, and the diffuse/Lambertian remainder), set via the surface's `MaterialPropertiesTable`.

### 7.2 The Study
Define at least three surface conditions and apply each to the tile's outer surfaces:
1. **Polished** (`polished`, `sigma_alpha = 0`) — favors total internal reflection, light bounces specularly.
2. **Ground/rough** (`ground`, e.g. `sigma_alpha ≈ 0.1–0.3 rad`) — more diffuse, light "forgets" its origin faster.
3. **Wrapped/painted** — model a reflective wrapping (e.g., a high-reflectivity `polishedfrontpainted` surface, reflectivity ~0.95) to mimic Tyvek or an ESR film.

For each condition, run the Week 5 position scan and record yield + uniformity, ideally at the thickness chosen as most promising in Week 6.

### 7.3 Discussion
- Polished surfaces with TIR tend to give *higher* peak efficiency near the SiPM but *worse* uniformity (strong position dependence).
- Diffuse/rough or wrapped surfaces "mix" the light and often improve uniformity at some cost to peak yield.
- This polished-vs-diffuse trade-off is the heart of the uniformity problem — make sure your plots show it clearly.

---

## Week 8: The Dimple

Many real tile designs use a *dimple* — a small hemispherical (or conical) indentation in the tile surface where the SiPM sits — precisely to flatten the position response.

### 8.1 Geometry
- Model the dimple as a subtraction: use `G4SubtractionSolid` to carve a small `G4Sphere` (or `G4Cons`) out of the tile at the SiPM location.
- Seat the SiPM volume inside or just below the dimple.
- **Sanity check:** Visualize the geometry (one of the few times to fall back on rendering) and confirm the dimple is a void in the scintillator with the SiPM coupled to it, not an overlap. Run `/geometry/test/run` to check for overlaps.

### 8.2 The Study
1. Run the full position scan **with** and **without** the dimple, holding thickness and surface finish fixed at your current best.
2. Optionally sweep dimple radius (e.g., 3, 5, 7 mm) to find where uniformity is best.

### 8.3 Discussion
The dimple's purpose is to reject photons arriving at very steep angles right above the SiPM (which otherwise over-respond to nearby hits) and to redirect more distant light toward the sensor. **Plot:** efficiency vs. distance-from-SiPM, dimple vs. no-dimple, on the same axes. The figure of merit is the *flatness* of the curve, not just its height.

---

## Week 9: Beam Energy & Particle Type Dependence

### 9.1 The Study
Using your best geometry so far, vary the incident particle and energy:
- **Particle type:** electrons, muons, possibly protons — different ionization profiles produce different light per unit length and different track topologies.
- **Energy:** sweep electron energy (e.g., 0.5, 1, 2, 5 MeV).

Watch for two distinct effects:
1. **Total light scales with deposited energy** (more energy → more scintillation photons), so raw detected counts will rise.
2. **Collection *efficiency* (detected/generated) should be largely geometry-driven** and roughly energy-independent — if it is *not*, that is a clue that range/straggling is changing where light is produced inside the tile (e.g., low-energy electrons stop near the surface).

### 9.2 Discussion
Separate "more light because more energy" from "different uniformity because of where the energy is deposited." Plot detection efficiency vs. energy (should be flatter) alongside detected-photon count vs. energy (should rise). This distinction matters when you compare to the lab source next week.

---

## Week 10: Modeling the Sr-90 Source Realistically

Now match the actual experimental driver. Sr-90 is a β⁻ emitter widely used as a test source.

### 10.1 The Sr-90 Beta Spectrum
Sr-90 decays to Y-90 (β⁻, endpoint ≈ 0.546 MeV), and Y-90 in turn decays β⁻ with a much higher endpoint (≈ 2.28 MeV). The electrons hitting your tile are therefore **not monoenergetic** — they follow a continuous beta spectrum dominated by the Y-90 high-energy tail. Modeling this correctly is important because low-energy betas barely penetrate the tile while the energetic Y-90 betas behave like minimum-ionizing particles.

Two options, in increasing realism:
1. **Decay-based:** Use the radioactive decay physics. Place a `Sr90` ion as the primary (`/gps/particle ion` with `/gps/ion 38 90`) and let `G4RadioactiveDecay` produce the betas, enabling the full Sr-90 → Y-90 → Zr-90 chain. This is the most faithful but requires care that the decay chain and the `G4RadioactiveDecayPhysics` constructor are registered in your physics list.
2. **Spectrum-based:** Supply the combined beta energy spectrum directly to GPS as a histogram:
   ```
   /gps/ene/type Arb
   /gps/hist/type energy
   # ... /gps/hist/point E prob   pairs defining the spectrum ...
   /gps/hist/inter Lin
   ```
   This is simpler and faster, and decouples the source spectrum from decay-physics bookkeeping.

Either way, **document which approach you chose and why**, and verify the sampled spectrum by histogramming the primary electron energies.

### 10.2 Beam Divergence
A real collimated source has angular spread. Use GPS to give the beam a finite divergence of a specified number of milliradians:
```
/gps/ang/type beam2d
/gps/ang/sigma_r 5 mrad   # set to your measured/assumed divergence
```
(Alternatively `/gps/ang/type iso` confined to a cone, depending on your collimator model.) Confirm that the divergence is small enough that the spot on the tile is consistent with the collimator geometry in the lab.

### 10.3 The Study
Repeat the key scans (position, and your best thickness/finish/dimple combination) using the Sr-90 source model instead of a monoenergetic beam. This is the simulation configuration you will actually compare against lab data.

---

## Week 11: Comparison with Lab Measurements & Tuning

### 11.1 Define Matching Observables
You cannot compare "number of optical photons" directly to a SiPM waveform. Decide on observables that exist in *both* sim and data, for example:
- Relative response vs. position (normalized so the comparison is shape-based, removing absolute calibration).
- Mean detected-photon count (sim) vs. mean integrated charge or photoelectron count (data), once you fold in SiPM PDE.

### 11.2 Fold in Detector Response (at least approximately)
The simulation produces photons arriving at the SiPM face; the lab measures photoelectrons. Bridge the gap by applying:
- **SiPM photon detection efficiency (PDE)** as a function of wavelength — weight each arriving photon by PDE(λ). This requires recording photon wavelength at the SiPM, so add that to your ntuple.
- Optionally, optical coupling/grease transmission and the SiPM active-area fill factor.

### 11.3 Tuning
Where sim and data disagree, the most likely "knobs" are the surface parameters (reflectivity, `sigma_alpha`), the scintillation yield, and the wrapping reflectivity — these are genuinely uncertain and reasonable to tune. Resist tuning parameters that are well known. Document every parameter you adjust and the justification. The goal is a model that reproduces the *position dependence* of the lab data, not just a single number.

---

## Week 12: Optimization & Final Recommendation

### 12.1 Define the Figure of Merit
Optimization needs a single, explicit objective. A reasonable choice that balances your two goals:
- Maximize mean light collection **and** minimize its spatial variation. For example, maximize (mean detected photoelectrons) while keeping non-uniformity (RMS/mean over the tile face, or max–min spread) below a target, or optimize a weighted combination. **Write down the figure of merit explicitly** before optimizing — otherwise "best" is undefined.

### 12.2 The Optimization Sweep
Using the validated model from Week 11, scan the remaining design choices to optimize the figure of merit:
- SiPM position (corner vs. edge-center vs. center; with/without dimple).
- Number of SiPMs (does a second sensor flatten the response enough to justify it?).
- Surface finish / wrapping combination.
- Tile thickness, revisited now that the source and response are realistic.

Because the parameter space is large, scan coarsely first, identify the promising region, then refine.

### 12.3 Final Deliverable
An updated report extending the original Week-4 deliverable, containing:
1. The 2-D efficiency maps for each major configuration studied (thickness, finish, dimple).
2. Plots isolating each dependence: efficiency & uniformity vs. thickness; vs. surface finish; dimple vs. no-dimple; vs. beam energy.
3. The Sr-90 source validation: simulated vs. measured position response, overlaid.
4. A stated figure of merit and the configuration (tile thickness, surface finish, dimple parameters, SiPM placement/count) that optimizes it.
5. A clear recommendation, with quantified expected light yield and uniformity, plus the dominant remaining uncertainties.

---

## Cross-Cutting Practices for Weeks 5–12

- **Reproducibility:** Keep every macro under version control alongside a one-line description of what study it belongs to. Seed the random engine and record the seed for any run you might need to reproduce exactly.
- **One variable at a time:** When a result surprises you, the only way to trust it is that nothing else changed.
- **Statistics:** Run enough events per grid point that the per-point efficiency uncertainty is small compared to the position-to-position variation you are trying to measure. Estimate this early — it sets your run times.
- **Validate the physics before the rendering:** As in the earlier weeks, confirm optical photons are actually being generated and reaching the SiPM (counts in the ntuple) before trusting any visualization.
- **Geometry overlaps:** Re-run `/geometry/test/run` after every geometry change, especially after adding the dimple and multiple SiPMs.
