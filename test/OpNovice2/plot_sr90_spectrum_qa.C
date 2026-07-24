#include <TCanvas.h>
#include <TChain.h>
#include <TGraph.h>
#include <TH1D.h>
#include <TLegend.h>
#include <TLine.h>
#include <TString.h>
#include <TStyle.h>
#include <TSystem.h>

#include <algorithm>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace {
struct SpectrumPoint {
  double energyMev = 0.0;
  double relative = 0.0;
};

std::vector<SpectrumPoint> ReadSpectrumCsv(const TString& path)
{
  std::vector<SpectrumPoint> points;
  std::ifstream in(path.Data());
  if (!in) {
    std::cerr << "Cannot read spectrum CSV: " << path << std::endl;
    return points;
  }

  std::string line;
  std::getline(in, line);
  while (std::getline(in, line)) {
    if (line.empty()) {
      continue;
    }
    std::stringstream ss(line);
    std::string energy;
    std::string sr90;
    std::string y90;
    std::string combined;
    std::string relative;
    std::getline(ss, energy, ',');
    std::getline(ss, sr90, ',');
    std::getline(ss, y90, ',');
    std::getline(ss, combined, ',');
    std::getline(ss, relative, ',');
    if (energy.empty() || relative.empty()) {
      continue;
    }
    points.push_back({std::stod(energy), std::stod(relative)});
  }
  return points;
}
}

void plot_sr90_spectrum_qa(const char* runDirArg = "scan_latest",
                           const char* spectrumCsvArg = "spectra/sr90_allowed_beta_v1.csv",
                           const char* outDirArg = "")
{
  TString runDir(runDirArg);
  TString spectrumCsv(spectrumCsvArg);
  TString outDir(outDirArg);
  if (outDir.IsNull()) {
    outDir = runDir;
  }
  gSystem->mkdir(outDir, kTRUE);

  TChain chain("scan");
  const Int_t added = chain.Add(Form("%s/outputs/*.root", runDir.Data()));
  if (added <= 0 || chain.GetEntries() <= 0) {
    std::cerr << "No scan ntuple entries found under " << runDir << "/outputs/*.root" << std::endl;
    return;
  }
  if (!chain.GetBranch("primary_kinetic_energy_mev")) {
    std::cerr << "Missing branch primary_kinetic_energy_mev in " << runDir << std::endl;
    return;
  }

  auto table = ReadSpectrumCsv(spectrumCsv);
  if (table.empty()) {
    return;
  }

  gStyle->SetOptStat(0);
  auto* canvas = new TCanvas("c_sr90_spectrum_qa", "Sr-90 spectrum QA", 1050, 760);
  canvas->SetLeftMargin(0.11);
  canvas->SetRightMargin(0.04);
  canvas->SetBottomMargin(0.11);
  canvas->SetTopMargin(0.07);

  auto* hist = new TH1D("h_sampled_sr90",
                        "Sr-90/Y-90 primary energy QA;primary kinetic energy [MeV];events",
                        120, 0.0, 2.4);
  hist->SetLineColor(kAzure + 2);
  hist->SetFillColorAlpha(kAzure + 1, 0.25);
  hist->SetLineWidth(2);
  chain.Draw("primary_kinetic_energy_mev>>h_sampled_sr90", "", "goff");

  const double histMax = std::max(1.0, hist->GetMaximum());
  const double inputMax = std::max_element(
                            table.begin(), table.end(),
                            [](const SpectrumPoint& a, const SpectrumPoint& b) {
                              return a.relative < b.relative;
                            })
                            ->relative;

  auto* graph = new TGraph(static_cast<Int_t>(table.size()));
  for (int i = 0; i < static_cast<int>(table.size()); ++i) {
    const double scaled = inputMax > 0.0 ? table[i].relative / inputMax * histMax : 0.0;
    graph->SetPoint(i, table[i].energyMev, scaled);
  }
  graph->SetLineColor(kOrange + 7);
  graph->SetLineWidth(3);

  hist->Draw("hist");
  graph->Draw("L same");

  auto* srLine = new TLine(0.546, 0.0, 0.546, histMax * 0.92);
  srLine->SetLineStyle(2);
  srLine->SetLineColor(kGray + 2);
  srLine->Draw("same");
  auto* yLine = new TLine(2.28, 0.0, 2.28, histMax * 0.92);
  yLine->SetLineStyle(2);
  yLine->SetLineColor(kGray + 2);
  yLine->Draw("same");

  auto* legend = new TLegend(0.57, 0.70, 0.94, 0.90);
  legend->SetBorderSize(0);
  legend->SetFillStyle(0);
  legend->AddEntry(hist, "Sampled primary energies", "lf");
  legend->AddEntry(graph, "Input spectrum table (scaled)", "l");
  legend->AddEntry(srLine, "Sr-90 endpoint 0.546 MeV", "l");
  legend->AddEntry(yLine, "Y-90 endpoint 2.28 MeV", "l");
  legend->Draw();

  std::cout << "Entries: " << hist->GetEntries()
            << ", sampled mean: " << hist->GetMean()
            << " MeV, sampled RMS: " << hist->GetRMS()
            << " MeV, sampled max: " << chain.GetMaximum("primary_kinetic_energy_mev")
            << " MeV" << std::endl;

  canvas->SaveAs(Form("%s/root_sr90_spectrum_qa.png", outDir.Data()));
  canvas->SaveAs(Form("%s/root_sr90_spectrum_qa.pdf", outDir.Data()));
}
