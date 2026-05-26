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
