//
// ********************************************************************
// * License and Disclaimer                                           *
// *                                                                  *
// * The  Geant4 software  is  copyright of the Copyright Holders  of *
// * the Geant4 Collaboration.  It is provided  under  the terms  and *
// * conditions of the Geant4 Software License,  included in the file *
// * LICENSE and available at  http://cern.ch/geant4/license .  These *
// * include a list of copyright holders.                             *
// *                                                                  *
// * Neither the authors of this software system, nor their employing *
// * institutes,nor the agencies providing financial support for this *
// * work  make  any representation or  warranty, express or implied, *
// * regarding  this  software system or assume any liability for its *
// * use.  Please see the license in the file  LICENSE  and URL above *
// * for the full disclaimer and the limitation of liability.         *
// *                                                                  *
// * This  code  implementation is the result of  the  scientific and *
// * technical work of the GEANT4 collaboration.                      *
// * By using,  copying,  modifying or  distributing the software (or *
// * any work based  on the software)  you  agree  to acknowledge its *
// * use  in  resulting  scientific  publications,  and indicate your *
// * acceptance of all terms of the Geant4 Software license.          *
// ********************************************************************
//
/// \file optical/OpNovice2/src/PrimaryGeneratorAction.cc
/// \brief Implementation of the PrimaryGeneratorAction class
//
//
//
//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

#include "PrimaryGeneratorAction.hh"

#include "PrimaryGeneratorMessenger.hh"

#include "G4Event.hh"
#include "G4OpticalPhoton.hh"
#include "G4ParticleDefinition.hh"
#include "G4ParticleGun.hh"
#include "G4ParticleTable.hh"
#include "G4SystemOfUnits.hh"
#include "Randomize.hh"

#include <algorithm>
#include <cmath>

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

PrimaryGeneratorAction::PrimaryGeneratorAction()
  : G4VUserPrimaryGeneratorAction(), fGeneralParticleSource(nullptr)
{
  G4int n_particle = 1;
  // fParticleGun = new G4ParticleGun(n_particle);
  fGeneralParticleSource = new G4GeneralParticleSource();
  fGeneralParticleSource->SetNumberOfParticles(n_particle);

  // create a messenger for this class
  // fGunMessenger = new PrimaryGeneratorMessenger(this);
  fParticleSourceMessenger = new PrimaryGeneratorMessenger(this);

  G4ParticleTable* particleTable = G4ParticleTable::GetParticleTable();
  G4ParticleDefinition* particle = particleTable->FindParticle("e+");

  // fParticleGun->SetParticleDefinition(particle);
  fGeneralParticleSource->SetParticleDefinition(particle);
  // fParticleGun->SetParticleTime(0.0 * ns);
  fGeneralParticleSource->SetParticleTime(0.0 * ns);
  // fParticleGun->SetParticlePosition(G4ThreeVector(0.0 * cm, 0.0 * cm, 0.0 * cm));
  fGeneralParticleSource->SetParticlePosition(G4ThreeVector(0.0 * cm, 0.0 * cm, 0.0 * cm));
  // fParticleGun->SetParticleMomentumDirection(G4ThreeVector(1., 0., 0.));
  // If problematic, check out https://geant4-forum.web.cern.ch/t/setparticlemomentumdirection-for-gps-is-not-working-properly/937
  // Also check out https://geant4-forum.web.cern.ch/t/it-is-not-convenient-that-g4generalparticlesource-class-can-not-set-particle-energy-moment-direction/9878
  // I implemented two helper functions, see `G4GeneralParticleSource.hh`, so I can use a similar syntax as `G4ParticleGun` here.
    // fGeneralParticleSource->GetCurrentSource()->GetAngDist()->SetParticleMomentumDirection(G4ThreeVector(0.0 * cm, 0.0 * cm, 0.0 * cm));
  fGeneralParticleSource->SetParticleMomentumDirection(G4ThreeVector(1., 0., 0.));
  // fParticleGun->SetParticleEnergy(500.0 * keV);
    // fGeneralParticleSource->GetCurrentSource()->GetEneDist()->SetMonoEnergy(500.0 * keV);
  fGeneralParticleSource->SetParticleEnergy(500.0 * keV);
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

PrimaryGeneratorAction::~PrimaryGeneratorAction()
{
  delete fGeneralParticleSource;
  delete fParticleSourceMessenger;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

void PrimaryGeneratorAction::GeneratePrimaries(G4Event* anEvent)
{
  if (fElectronEnergyMode == "sr90Beta") {
    auto particle = fGeneralParticleSource->GetParticleDefinition();
    if (!particle || particle->GetParticleName() != "e-") {
      G4ExceptionDescription msg;
      msg << "Electron energy mode sr90Beta requires /gun/particle e-.";
      G4Exception("PrimaryGeneratorAction::GeneratePrimaries",
                  "OpNovice2_Gun_001",
                  FatalException,
                  msg);
    }
    fGeneralParticleSource->SetParticleEnergy(SampleSr90BetaEnergy());
  }

  if (fRandomDirection) {
    G4double theta = CLHEP::halfpi * G4UniformRand();
    G4double phi = CLHEP::twopi * G4UniformRand();
    G4double x = std::cos(theta);
    G4double y = std::sin(theta) * std::sin(phi);
    G4double z = std::sin(theta) * std::cos(phi);
    G4ThreeVector dir(x, y, z);
    fGeneralParticleSource->SetParticleMomentumDirection(dir);
  }
  if (fGeneralParticleSource->GetParticleDefinition() == G4OpticalPhoton::OpticalPhotonDefinition()) {
    if (fPolarized)
      SetOptPhotonPolar(fPolarization);
    else
      SetOptPhotonPolar();
  }
  fGeneralParticleSource->GeneratePrimaryVertex(anEvent);
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

void PrimaryGeneratorAction::SetOptPhotonPolar()
{
  G4double angle = G4UniformRand() * 360.0 * deg;
  SetOptPhotonPolar(angle);
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

void PrimaryGeneratorAction::SetOptPhotonPolar(G4double angle)
{
  if (fGeneralParticleSource->GetParticleDefinition() != G4OpticalPhoton::OpticalPhotonDefinition()) {
    G4ExceptionDescription ed;
    ed << "The particleGun is not an opticalphoton.";
    G4Exception("PrimaryGeneratorAction::SetOptPhotonPolar", "OpNovice2_004", JustWarning, ed);
    return;
  }

  fPolarized = true;
  fPolarization = angle;

  G4ThreeVector normal(1., 0., 0.);
  G4ThreeVector kphoton = fGeneralParticleSource->GetParticleMomentumDirection();
  G4ThreeVector product = normal.cross(kphoton);
  G4double modul2 = product * product;

  G4ThreeVector e_perpend(0., 0., 1.);
  if (modul2 > 0.) e_perpend = (1. / std::sqrt(modul2)) * product;
  G4ThreeVector e_paralle = e_perpend.cross(kphoton);

  G4ThreeVector polar = std::cos(angle) * e_paralle + std::sin(angle) * e_perpend;
  fGeneralParticleSource->SetParticlePolarization(polar);
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void PrimaryGeneratorAction::SetRandomDirection(G4bool val)
{
  fRandomDirection = val;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void PrimaryGeneratorAction::SetElectronEnergyMode(const G4String& mode)
{
  if (mode == "fixed") {
    fElectronEnergyMode = mode;
  }
  else if (mode == "sr90" || mode == "sr90Beta") {
    fElectronEnergyMode = "sr90Beta";
  }
  else {
    G4ExceptionDescription msg;
    msg << "Invalid electron energy mode: " << mode
        << ". Use fixed or sr90Beta.";
    G4Exception("PrimaryGeneratorAction::SetElectronEnergyMode",
                "OpNovice2_Gun_002",
                FatalException,
                msg);
  }

  G4cout << "Electron energy mode set to " << fElectronEnergyMode << G4endl;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
G4double PrimaryGeneratorAction::SampleSr90BetaEnergy() const
{
  // Empirical approximation of the supplied Sr-90 beta spectrum plot.
  // Energies are in MeV and weights are relative emission density.
  static const G4double energyMeV[] = {
    0.00, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50,
    0.65, 0.80, 1.00, 1.20, 1.40, 1.60, 1.80, 2.00,
    2.20, 2.28
  };
  static const G4double weight[] = {
    0.00, 0.20, 0.80, 1.00, 0.88, 0.50, 0.32, 0.22,
    0.20, 0.22, 0.18, 0.13, 0.08, 0.045, 0.025, 0.012,
    0.004, 0.00
  };
  static const G4int n = sizeof(energyMeV) / sizeof(energyMeV[0]);

  G4double totalArea = 0.0;
  for (G4int i = 0; i < n - 1; ++i) {
    totalArea += 0.5 * (weight[i] + weight[i + 1]) * (energyMeV[i + 1] - energyMeV[i]);
  }

  G4double target = G4UniformRand() * totalArea;
  for (G4int i = 0; i < n - 1; ++i) {
    const G4double dx = energyMeV[i + 1] - energyMeV[i];
    const G4double w0 = weight[i];
    const G4double w1 = weight[i + 1];
    const G4double segmentArea = 0.5 * (w0 + w1) * dx;
    if (target > segmentArea) {
      target -= segmentArea;
      continue;
    }

    const G4double slope = (w1 - w0) / dx;
    G4double offset = 0.0;
    if (std::abs(slope) < 1.e-12) {
      offset = (w0 > 0.) ? target / w0 : G4UniformRand() * dx;
    }
    else {
      const G4double discriminant = std::max(0.0, w0 * w0 + 2. * slope * target);
      offset = (-w0 + std::sqrt(discriminant)) / slope;
      if (offset < 0. || offset > dx) {
        offset = G4UniformRand() * dx;
      }
    }

    return (energyMeV[i] + offset) * MeV;
  }

  return energyMeV[n - 1] * MeV;
}
