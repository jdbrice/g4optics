#include <TCanvas.h>
#include <TChain.h>
#include <TH1D.h>
#include <TLegend.h>
#include <TLine.h>
#include <TString.h>
#include <TStyle.h>
#include <TSystem.h>

#include <algorithm>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace {
struct SpectrumPoint {
  double energy = 0.0;
  double pdf = 0.0;
};

struct SpectrumStats {
  double area = 0.0;
  double mean = 0.0;
  double rms = 0.0;
};

std::vector<std::string> SplitCsvLine(const std::string& line)
{
  std::vector<std::string> fields;
  std::stringstream ss(line);
  std::string field;
  while (std::getline(ss, field, ',')) {
    fields.push_back(field);
  }
  return fields;
}

std::vector<SpectrumPoint> ReadSpectrumCsv(const TString& path)
{
  std::ifstream in(path.Data());
  if (!in) {
    std::cerr << "Cannot read spectrum CSV: " << path << std::endl;
    return {};
  }

  std::string line;
  std::getline(in, line);
  std::vector<SpectrumPoint> points;
  while (std::getline(in, line)) {
    if (line.empty()) {
      continue;
    }
    const auto fields = SplitCsvLine(line);
    if (fields.size() < 4) {
      continue;
    }
    points.push_back({std::stod(fields[0]), std::stod(fields[3])});
  }
  return points;
}

SpectrumStats IntegrateSpectrum(const std::vector<SpectrumPoint>& points)
{
  SpectrumStats stats;
  double sumE = 0.0;
  double sumE2 = 0.0;
  for (size_t i = 1; i < points.size(); ++i) {
    const auto& a = points[i - 1];
    const auto& b = points[i];
    const double dx = b.energy - a.energy;
    const double area = 0.5 * (a.pdf + b.pdf) * dx;
    stats.area += area;
    sumE += 0.5 * (a.energy * a.pdf + b.energy * b.pdf) * dx;
    sumE2 += 0.5 * (a.energy * a.energy * a.pdf + b.energy * b.energy * b.pdf) * dx;
  }
  if (stats.area > 0.0) {
    stats.mean = sumE / stats.area;
    stats.rms = std::sqrt(std::max(0.0, sumE2 / stats.area - stats.mean * stats.mean));
  }
  return stats;
}

void FillExpectedCounts(TH1D* expected,
                        const std::vector<SpectrumPoint>& points,
                        double entries,
                        double area)
{
  if (!expected || points.size() < 2 || entries <= 0.0 || area <= 0.0) {
    return;
  }

  for (size_t i = 1; i < points.size(); ++i) {
    const auto& a = points[i - 1];
    const auto& b = points[i];
    const double segmentArea = 0.5 * (a.pdf + b.pdf) * (b.energy - a.energy);
    expected->AddBinContent(expected->FindBin(0.5 * (a.energy + b.energy)),
                            entries * segmentArea / area);
  }
}

bool AddEnergyHistogram(TChain& chain, const char* expression, TH1D* hist)
{
  if (!hist) {
    return false;
  }
  chain.Draw(Form("%s>>%s", expression, hist->GetName()), "", "goff");
  return hist->GetEntries() > 0.0;
}
}

void plot_sr90_decay_spectrum_qa(
  const char* decayRunDirArg = "scan_latest",
  const char* spectrumRunDirArg = "",
  const char* spectrumCsvArg = "spectra/sr90_allowed_beta_v1.csv",
  const char* outDirArg = "")
{
  TString decayRunDir(decayRunDirArg);
  TString spectrumRunDir(spectrumRunDirArg);
  TString spectrumCsv(spectrumCsvArg);
  TString outDir(outDirArg);
  if (outDir.IsNull()) {
    outDir = decayRunDir;
  }
  gSystem->mkdir(outDir, kTRUE);

  TChain decayChain("decay_betas");
  const Int_t decayFiles = decayChain.Add(Form("%s/outputs/*.root", decayRunDir.Data()));
  if (decayFiles <= 0 || decayChain.GetEntries() <= 0) {
    std::cerr << "No decay_betas ntuple entries found under "
              << decayRunDir << "/outputs/*.root" << std::endl;
    return;
  }
  if (!decayChain.GetBranch("kinetic_energy_mev")) {
    std::cerr << "Missing branch kinetic_energy_mev in decay_betas ntuple under "
              << decayRunDir << std::endl;
    return;
  }

  const auto spectrum = ReadSpectrumCsv(spectrumCsv);
  const auto inputStats = IntegrateSpectrum(spectrum);
  if (spectrum.empty() || inputStats.area <= 0.0) {
    return;
  }

  TChain spectrumChain("scan");
  const bool useSpectrumRun = !spectrumRunDir.IsNull()
                              && spectrumChain.Add(Form("%s/outputs/*.root",
                                                        spectrumRunDir.Data())) > 0
                              && spectrumChain.GetEntries() > 0
                              && spectrumChain.GetBranch("primary_kinetic_energy_mev");

  gStyle->SetOptStat(0);

  auto* decayHist = new TH1D("h_sr90_decay_beta_energy",
                             "Sr-90 decay beta spectrum QA;beta kinetic energy [MeV];counts / 0.02 MeV",
                             120, 0.0, 2.4);
  decayHist->SetLineColor(kAzure + 2);
  decayHist->SetFillColorAlpha(kAzure + 1, 0.25);
  decayHist->SetLineWidth(2);
  AddEnergyHistogram(decayChain, "kinetic_energy_mev", decayHist);

  auto* expected = new TH1D("h_sr90_decay_expected_energy",
                            "expected from input spectrum",
                            decayHist->GetNbinsX(),
                            decayHist->GetXaxis()->GetXmin(),
                            decayHist->GetXaxis()->GetXmax());
  FillExpectedCounts(expected, spectrum, decayHist->GetEntries(), inputStats.area);
  expected->SetLineColor(kOrange + 7);
  expected->SetLineWidth(3);

  TH1D* spectrumHist = nullptr;
  if (useSpectrumRun) {
    spectrumHist = new TH1D("h_sr90_spectrum_sampled_energy",
                            "sampled sr90-spectrum primary energies",
                            decayHist->GetNbinsX(),
                            decayHist->GetXaxis()->GetXmin(),
                            decayHist->GetXaxis()->GetXmax());
    spectrumHist->SetLineColor(kGreen + 2);
    spectrumHist->SetLineStyle(7);
    spectrumHist->SetLineWidth(3);
    AddEnergyHistogram(spectrumChain, "primary_kinetic_energy_mev", spectrumHist);
    if (spectrumHist->Integral() > 0.0 && decayHist->Integral() > 0.0) {
      spectrumHist->Scale(decayHist->Integral() / spectrumHist->Integral());
    }
  }

  auto* canvas = new TCanvas("c_sr90_decay_spectrum_qa",
                             "Sr-90 decay beta spectrum QA",
                             1100, 780);
  canvas->SetLeftMargin(0.11);
  canvas->SetRightMargin(0.04);
  canvas->SetBottomMargin(0.11);
  canvas->SetTopMargin(0.07);

  double ymax = std::max(decayHist->GetMaximum(), expected->GetMaximum());
  if (spectrumHist) {
    ymax = std::max(ymax, spectrumHist->GetMaximum());
  }
  decayHist->SetMaximum(std::max(1.0, ymax) * 1.20);
  decayHist->Draw("hist");
  expected->Draw("hist same");
  if (spectrumHist) {
    spectrumHist->Draw("hist same");
  }

  auto* srLine = new TLine(0.546, 0.0, 0.546, decayHist->GetMaximum() * 0.90);
  srLine->SetLineStyle(2);
  srLine->SetLineColor(kGray + 2);
  srLine->Draw("same");
  auto* yLine = new TLine(2.28, 0.0, 2.28, decayHist->GetMaximum() * 0.90);
  yLine->SetLineStyle(2);
  yLine->SetLineColor(kGray + 2);
  yLine->Draw("same");

  auto* legend = new TLegend(0.54, 0.64, 0.94, 0.90);
  legend->SetBorderSize(0);
  legend->SetFillStyle(0);
  legend->AddEntry(decayHist, "sr90-decay beta ntuple", "lf");
  legend->AddEntry(expected, "sr90_allowed_beta_v1 expected", "l");
  if (spectrumHist) {
    legend->AddEntry(spectrumHist, "sr90-spectrum sampled ntuple (scaled)", "l");
  }
  legend->AddEntry(srLine, "Sr-90 endpoint 0.546 MeV", "l");
  legend->AddEntry(yLine, "Y-90 endpoint 2.28 MeV", "l");
  legend->Draw();

  canvas->SaveAs(Form("%s/root_sr90_decay_spectrum_qa.png", outDir.Data()));
  canvas->SaveAs(Form("%s/root_sr90_decay_spectrum_qa.pdf", outDir.Data()));

  std::ofstream out(Form("%s/root_sr90_decay_spectrum_qa_summary.csv", outDir.Data()));
  out << "source,entries,mean_mev,rms_mev,min_mev,max_mev\n";
  out << std::setprecision(12);
  out << "decay_betas," << decayHist->GetEntries() << "," << decayHist->GetMean()
      << "," << decayHist->GetRMS() << ","
      << decayChain.GetMinimum("kinetic_energy_mev") << ","
      << decayChain.GetMaximum("kinetic_energy_mev") << "\n";
  out << "input_table," << inputStats.area << "," << inputStats.mean << ","
      << inputStats.rms << ",0,2.28\n";
  if (spectrumHist) {
    out << "sr90_spectrum_sampled," << spectrumHist->GetEntries() << ","
        << spectrumHist->GetMean() << ","
        << spectrumHist->GetRMS() << ","
        << spectrumChain.GetMinimum("primary_kinetic_energy_mev") << ","
        << spectrumChain.GetMaximum("primary_kinetic_energy_mev") << "\n";
  }

  std::cout << "Decay beta spectrum: entries=" << decayHist->GetEntries()
            << ", mean=" << decayHist->GetMean()
            << " MeV, RMS=" << decayHist->GetRMS()
            << " MeV, max=" << decayChain.GetMaximum("kinetic_energy_mev")
            << " MeV" << std::endl;
  std::cout << "Wrote Sr-90 decay spectrum QA plots to " << outDir << std::endl;
}
