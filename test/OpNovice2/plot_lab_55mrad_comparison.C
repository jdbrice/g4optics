#ifndef OPNOVICE2_PLOT_LAB_55MRAD_COMPARISON_C
#define OPNOVICE2_PLOT_LAB_55MRAD_COMPARISON_C

#include "plot_efficiency_map.C"

#include <TAxis.h>
#include <TCanvas.h>
#include <TGraph.h>
#include <TGraphErrors.h>
#include <TLegend.h>
#include <TPaveText.h>
#include <TROOT.h>
#include <TStyle.h>
#include <TSystem.h>

#include <fstream>
#include <map>
#include <utility>

namespace {

struct LabSampleSpec {
  TString id;
  TString title;
  TString labCsv;
  TString simDir;
  double tileXmm = 0.0;
  double tileYmm = 0.0;
};

struct LabPoint {
  int index = 0;
  double x = kNaN;
  double y = kNaN;
  double response = kNaN;
  double uncertainty = kNaN;
  bool inside = false;
};

struct MatchedPoint {
  LabPoint lab;
  double simDetPerEvent = kNaN;
};

struct MatchMetrics {
  int n = 0;
  double scaleAll = kNaN;
  double allNormRmse = kNaN;
  double allPearson = kNaN;
  double insideNormRmse = kNaN;
  double insidePearson = kNaN;
};

TString JoinPath(TString base, const TString& child)
{
  if (base.EndsWith("/")) base.Chop();
  if (base.IsNull() || base == ".") return child;
  return Form("%s/%s", base.Data(), child.Data());
}

std::string CoordKey(double x, double y)
{
  return Form("%.6f,%.6f", x, y);
}

bool IsInside(double x, double y, double tileXmm, double tileYmm)
{
  return std::abs(x) <= 0.5 * tileXmm && std::abs(y) <= 0.5 * tileYmm;
}

std::vector<LabPoint> ReadLabResponseCsv(const TString& path, double tileXmm, double tileYmm)
{
  std::ifstream in(path.Data());
  if (!in) {
    throw std::runtime_error(std::string("Cannot open lab CSV: ") + path.Data());
  }

  std::string headerLine;
  if (!std::getline(in, headerLine)) {
    throw std::runtime_error(std::string("Empty lab CSV: ") + path.Data());
  }

  const auto header = SplitCsvLine(headerLine);
  std::map<std::string, int> columns;
  for (size_t i = 0; i < header.size(); ++i) {
    columns[Trim(header[i])] = static_cast<int>(i);
  }

  const int xCol = ColumnIndex(columns, "X");
  const int yCol = ColumnIndex(columns, "Y");
  const int responseCol = ColumnIndex(columns, "Dark_Current_Subtracted_Integral");
  const int uncertaintyCol = ColumnIndex(columns, "Uncertainty");

  std::vector<LabPoint> points;
  std::string line;
  int index = 0;
  while (std::getline(in, line)) {
    if (Trim(line).empty()) continue;
    auto fields = SplitCsvLine(line);
    if (fields.size() < header.size()) fields.resize(header.size());

    LabPoint point;
    point.index = ++index;
    point.x = ParseDouble(fields[xCol]);
    point.y = ParseDouble(fields[yCol]);
    point.response = ParseDouble(fields[responseCol]);
    point.uncertainty = ParseDouble(fields[uncertaintyCol]);
    point.inside = IsInside(point.x, point.y, tileXmm, tileYmm);
    points.push_back(point);
  }
  return points;
}

double DetectedPerEvent(const EfficiencyPoint& point)
{
  return std::isfinite(point.events) && point.events != 0.0 ? point.detected / point.events : kNaN;
}

std::vector<MatchedPoint> MatchLabToSimulation(const std::vector<LabPoint>& lab,
                                               const std::vector<EfficiencyPoint>& sim)
{
  std::map<std::string, EfficiencyPoint> simByCoord;
  for (const auto& point : sim) {
    simByCoord[CoordKey(point.xMm, point.yMm)] = point;
  }

  std::vector<MatchedPoint> matched;
  for (const auto& labPoint : lab) {
    const auto found = simByCoord.find(CoordKey(labPoint.x, labPoint.y));
    if (found == simByCoord.end()) {
      std::cerr << "Missing simulation coordinate for lab point "
                << labPoint.index << ": x=" << labPoint.x
                << ", y=" << labPoint.y << std::endl;
      continue;
    }
    matched.push_back({labPoint, DetectedPerEvent(found->second)});
  }
  return matched;
}

double WeightedScale(const std::vector<MatchedPoint>& points, bool insideOnly, bool outsideOnly)
{
  double numerator = 0.0;
  double denominator = 0.0;
  for (const auto& point : points) {
    if (insideOnly && !point.lab.inside) continue;
    if (outsideOnly && point.lab.inside) continue;
    if (!std::isfinite(point.lab.response) || !std::isfinite(point.simDetPerEvent)) continue;
    const double sigma = point.lab.uncertainty;
    const double weight = std::isfinite(sigma) && sigma > 0.0 ? 1.0 / (sigma * sigma) : 1.0;
    numerator += weight * point.simDetPerEvent * point.lab.response;
    denominator += weight * point.simDetPerEvent * point.simDetPerEvent;
  }
  return denominator != 0.0 ? numerator / denominator : kNaN;
}

std::vector<double> LabValues(const std::vector<MatchedPoint>& points, bool insideOnly)
{
  std::vector<double> values;
  for (const auto& point : points) {
    if (insideOnly && !point.lab.inside) continue;
    if (std::isfinite(point.lab.response)) values.push_back(point.lab.response);
  }
  return values;
}

std::vector<double> SimValues(const std::vector<MatchedPoint>& points, bool insideOnly)
{
  std::vector<double> values;
  for (const auto& point : points) {
    if (insideOnly && !point.lab.inside) continue;
    if (std::isfinite(point.simDetPerEvent)) values.push_back(point.simDetPerEvent);
  }
  return values;
}

double NormalizedRmse(const std::vector<double>& lab, const std::vector<double>& sim)
{
  if (lab.empty() || sim.empty()) return kNaN;
  const double maxLab = *std::max_element(lab.begin(), lab.end(), [](double a, double b) {
    return std::abs(a) < std::abs(b);
  });
  const double maxSim = *std::max_element(sim.begin(), sim.end(), [](double a, double b) {
    return std::abs(a) < std::abs(b);
  });
  const double labScale = std::abs(maxLab);
  const double simScale = std::abs(maxSim);
  if (labScale == 0.0 || simScale == 0.0) return kNaN;

  double sum = 0.0;
  int n = 0;
  for (size_t i = 0; i < lab.size() && i < sim.size(); ++i) {
    if (!std::isfinite(lab[i]) || !std::isfinite(sim[i])) continue;
    const double residual = lab[i] / labScale - sim[i] / simScale;
    sum += residual * residual;
    ++n;
  }
  return n > 0 ? std::sqrt(sum / n) : kNaN;
}

double Pearson(const std::vector<double>& xs, const std::vector<double>& ys)
{
  if (xs.size() < 2 || ys.size() < 2) return kNaN;
  double sumX = 0.0;
  double sumY = 0.0;
  int n = 0;
  for (size_t i = 0; i < xs.size() && i < ys.size(); ++i) {
    if (!std::isfinite(xs[i]) || !std::isfinite(ys[i])) continue;
    sumX += xs[i];
    sumY += ys[i];
    ++n;
  }
  if (n < 2) return kNaN;
  const double meanX = sumX / n;
  const double meanY = sumY / n;

  double cov = 0.0;
  double varX = 0.0;
  double varY = 0.0;
  for (size_t i = 0; i < xs.size() && i < ys.size(); ++i) {
    if (!std::isfinite(xs[i]) || !std::isfinite(ys[i])) continue;
    const double dx = xs[i] - meanX;
    const double dy = ys[i] - meanY;
    cov += dx * dy;
    varX += dx * dx;
    varY += dy * dy;
  }
  const double denom = std::sqrt(varX * varY);
  return denom != 0.0 ? cov / denom : kNaN;
}

MatchMetrics ComputeMetrics(const std::vector<MatchedPoint>& points)
{
  MatchMetrics metrics;
  metrics.n = static_cast<int>(points.size());
  metrics.scaleAll = WeightedScale(points, false, false);
  const auto labAll = LabValues(points, false);
  const auto simAll = SimValues(points, false);
  const auto labInside = LabValues(points, true);
  const auto simInside = SimValues(points, true);
  metrics.allNormRmse = NormalizedRmse(labAll, simAll);
  metrics.allPearson = Pearson(labAll, simAll);
  metrics.insideNormRmse = NormalizedRmse(labInside, simInside);
  metrics.insidePearson = Pearson(labInside, simInside);
  return metrics;
}

double MaxLabAbs(const std::vector<MatchedPoint>& points)
{
  double maxValue = 0.0;
  for (const auto& point : points) {
    if (std::isfinite(point.lab.response)) maxValue = std::max(maxValue, std::abs(point.lab.response));
  }
  return maxValue;
}

void DrawSamplePanel(const LabSampleSpec& sample,
                     const std::vector<MatchedPoint>& points,
                     const MatchMetrics& metrics)
{
  const double labMax = MaxLabAbs(points);
  auto* labGraph = new TGraphErrors();
  auto* simGraph = new TGraph();

  double yMin = std::numeric_limits<double>::infinity();
  double yMax = -std::numeric_limits<double>::infinity();
  int n = 0;
  for (const auto& point : points) {
    if (!std::isfinite(point.lab.response) || !std::isfinite(point.simDetPerEvent) || labMax == 0.0) {
      continue;
    }
    const double x = point.lab.index;
    const double labY = 100.0 * point.lab.response / labMax;
    const double labErr = std::isfinite(point.lab.uncertainty) ? 100.0 * point.lab.uncertainty / labMax : 0.0;
    const double simY = 100.0 * point.simDetPerEvent * metrics.scaleAll / labMax;

    labGraph->SetPoint(n, x, labY);
    labGraph->SetPointError(n, 0.0, labErr);
    simGraph->SetPoint(n, x, simY);
    yMin = std::min(yMin, std::min(labY - labErr, simY));
    yMax = std::max(yMax, std::max(labY + labErr, simY));
    ++n;
  }

  if (!std::isfinite(yMin) || !std::isfinite(yMax)) {
    yMin = 0.0;
    yMax = 100.0;
  }
  const double span = yMax - yMin;
  const double pad = span > 0.0 ? 0.15 * span : 10.0;
  yMin = std::min(0.0, yMin - pad);
  yMax += pad;

  gPad->SetLeftMargin(0.12);
  gPad->SetRightMargin(0.04);
  gPad->SetBottomMargin(0.13);
  gPad->SetTopMargin(0.12);
  gPad->SetGrid(1, 1);

  auto* frame = gPad->DrawFrame(
    0.5,
    yMin,
    points.size() + 0.5,
    yMax,
    Form("%s;lab scan point index;response normalized to lab max [%%]", sample.title.Data()));
  frame->GetXaxis()->SetNdivisions(static_cast<int>(points.size()));
  frame->GetYaxis()->SetTitleOffset(0.88);

  labGraph->SetMarkerStyle(20);
  labGraph->SetMarkerSize(0.95);
  labGraph->SetMarkerColor(kBlack);
  labGraph->SetLineColor(kBlack);
  labGraph->SetLineWidth(1);
  labGraph->Draw("P same");

  simGraph->SetMarkerStyle(24);
  simGraph->SetMarkerSize(0.95);
  simGraph->SetMarkerColor(kRed + 1);
  simGraph->SetLineColor(kRed + 1);
  simGraph->SetLineWidth(2);
  simGraph->Draw("LP same");

  auto* legend = new TLegend(0.52, 0.75, 0.91, 0.89);
  legend->SetBorderSize(0);
  legend->SetFillStyle(0);
  legend->SetTextSize(0.034);
  legend->AddEntry(labGraph, "lab data", "pe");
  legend->AddEntry(simGraph, "simulation, 55 mrad", "lp");
  legend->Draw();

  auto* note = new TPaveText(0.15, 0.73, 0.50, 0.89, "NDC");
  note->SetFillStyle(0);
  note->SetBorderSize(0);
  note->SetTextAlign(12);
  note->SetTextSize(0.031);
  note->AddText(Form("all RMSE = %.1f%%, r = %.3f", 100.0 * metrics.allNormRmse, metrics.allPearson));
  note->AddText(Form("inside RMSE = %.1f%%, r = %.3f", 100.0 * metrics.insideNormRmse, metrics.insidePearson));
  note->AddText(Form("scale = %.3g lab / det/event", metrics.scaleAll));
  note->Draw();
}

void WriteSummaryCsv(const std::vector<LabSampleSpec>& samples,
                     const std::vector<MatchMetrics>& metrics,
                     const TString& outPath)
{
  std::ofstream out(outPath.Data());
  out << "sample_id,n_points,scale_all,all_normalized_rmse,all_pearson_r,"
      << "inside_normalized_rmse,inside_pearson_r\n";
  out << std::setprecision(12);
  for (size_t i = 0; i < samples.size(); ++i) {
    out << samples[i].id << ','
        << metrics[i].n << ','
        << metrics[i].scaleAll << ','
        << metrics[i].allNormRmse << ','
        << metrics[i].allPearson << ','
        << metrics[i].insideNormRmse << ','
        << metrics[i].insidePearson << '\n';
  }
}

}  // namespace

// Usage from test/OpNovice2:
//   root -b -q 'plot_lab_55mrad_comparison.C()'
//
// Or from the repository root:
//   root -b -q 'test/OpNovice2/plot_lab_55mrad_comparison.C("test/OpNovice2")'
void plot_lab_55mrad_comparison(const char* baseDirArg = ".",
                                const char* outDirArg = "lab_run/root_plots")
{
  gStyle->SetOptStat(0);
  gStyle->SetPalette(kBird);

  const TString baseDir(baseDirArg ? baseDirArg : ".");
  const TString outDir = JoinPath(baseDir, outDirArg ? outDirArg : "lab_run/root_plots");
  gSystem->mkdir(outDir.Data(), true);

  const std::vector<LabSampleSpec> samples = {
    {
      "lab_5x5x16",
      "5x5x16 calibration",
      "lab_data/5x5x1.6_painted_undimpled_opticalGrease/responses_tile_50.000000x50.000000_pattern_eighth_grid_16mm_painted_optical_grease_tile_OM_1776182096.csv",
      "lab_run/lab_5x5x16_55mrad",
      50.0,
      50.0,
    },
    {
      "lab_5x5x4",
      "5x5x4 validation",
      "lab_data/5x5x0.4_painted_undimpled_opticalGrease/responses_tile_50.000000x50.000000_pattern_eighth_grid_4mm_no_dimple_painted_optical_grease_JW_1776360791.csv",
      "lab_run/lab_5x5x4_55mrad",
      50.0,
      50.0,
    },
    {
      "lab_10x10x4",
      "10x10x4 validation",
      "lab_data/10x10x0.4_painted_undimpled_opticalGrease/responses_tile_100.000000x100.000000_pattern_eighth_grid_4mm_painted_undimpled_optical_grease_RERUN_2_JW_1781188758.csv",
      "lab_run/lab_10x10x4_55mrad",
      100.0,
      100.0,
    },
    {
      "lab_10x10x16",
      "10x10x16 validation",
      "lab_data/10x10x1.6_painted_undimpled_opticalGrease/responses_tile_100.000000x100.000000_pattern_eighth_grid_16mm_painted_undimpled_optical_grease_RERUN_5_SC_1781802404.csv",
      "lab_run/lab_10x10x16_55mrad",
      100.0,
      100.0,
    },
  };

  std::vector<std::vector<MatchedPoint>> allMatches;
  std::vector<MatchMetrics> allMetrics;
  for (const auto& sample : samples) {
    const auto lab = ReadLabResponseCsv(JoinPath(baseDir, sample.labCsv), sample.tileXmm, sample.tileYmm);
    const auto sim = ReadEfficiencyMap(JoinPath(JoinPath(baseDir, sample.simDir), "efficiency_map.csv"));
    const auto matched = MatchLabToSimulation(lab, sim);
    const auto metrics = ComputeMetrics(matched);
    allMatches.push_back(matched);
    allMetrics.push_back(metrics);

    std::cout << sample.id << ": n=" << metrics.n
              << ", all RMSE=" << 100.0 * metrics.allNormRmse << "%"
              << ", all r=" << metrics.allPearson
              << ", inside RMSE=" << 100.0 * metrics.insideNormRmse << "%"
              << ", inside r=" << metrics.insidePearson
              << std::endl;
  }

  auto* canvas = new TCanvas("c_lab_55mrad_comparison", "55 mrad simulation vs lab data", 1800, 1200);
  canvas->Divide(2, 2, 0.01, 0.01);
  for (size_t i = 0; i < samples.size(); ++i) {
    canvas->cd(static_cast<int>(i + 1));
    DrawSamplePanel(samples[i], allMatches[i], allMetrics[i]);
  }
  SaveCanvas(canvas, outDir, "lab_55mrad_vs_lab_profiles");
  WriteSummaryCsv(samples, allMetrics, JoinPath(outDir, "lab_55mrad_vs_lab_profiles_summary.csv"));
}

#endif  // OPNOVICE2_PLOT_LAB_55MRAD_COMPARISON_C
