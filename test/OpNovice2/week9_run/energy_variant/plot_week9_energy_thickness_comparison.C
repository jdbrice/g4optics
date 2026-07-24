#ifndef OPNOVICE2_PLOT_WEEK9_ENERGY_THICKNESS_COMPARISON_C
#define OPNOVICE2_PLOT_WEEK9_ENERGY_THICKNESS_COMPARISON_C

#include <TAxis.h>
#include <TCanvas.h>
#include <TGraph.h>
#include <TLegend.h>
#include <TMultiGraph.h>
#include <TROOT.h>
#include <TString.h>
#include <TStyle.h>
#include <TSystem.h>

#include <algorithm>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

namespace {

struct SummaryPoint {
  double energyMev = std::numeric_limits<double>::quiet_NaN();
  std::string label;
  std::string region;
  double meanEffPercent = std::numeric_limits<double>::quiet_NaN();
  double rmsOverMeanPercent = std::numeric_limits<double>::quiet_NaN();
  double detectedPerEvent = std::numeric_limits<double>::quiet_NaN();
  double scintillationPerEvent = std::numeric_limits<double>::quiet_NaN();
};

struct ThicknessRun {
  double thicknessMm = std::numeric_limits<double>::quiet_NaN();
  std::string label;
  std::vector<SummaryPoint> points;
};

struct Range {
  double min = std::numeric_limits<double>::infinity();
  double max = -std::numeric_limits<double>::infinity();

  void Add(double value)
  {
    if (!std::isfinite(value)) return;
    min = std::min(min, value);
    max = std::max(max, value);
  }

  bool Valid() const
  {
    return std::isfinite(min) && std::isfinite(max) && min <= max;
  }
};

enum class Metric {
  kMeanEfficiencyPercent,
  kRelativeNonUniformityPercent,
  kDetectedPerEvent,
  kScintillationPerEvent
};

std::vector<std::string> SplitCsvLine(const std::string& line)
{
  std::vector<std::string> fields;
  std::stringstream stream(line);
  std::string field;
  while (std::getline(stream, field, ',')) {
    fields.push_back(field);
  }
  return fields;
}

double ParseDouble(const std::vector<std::string>& fields,
                   const std::unordered_map<std::string, size_t>& columns,
                   const std::string& name)
{
  const auto it = columns.find(name);
  if (it == columns.end() || it->second >= fields.size()) {
    throw std::runtime_error("Missing CSV column: " + name);
  }
  return std::stod(fields[it->second]);
}

std::string ParseString(const std::vector<std::string>& fields,
                        const std::unordered_map<std::string, size_t>& columns,
                        const std::string& name)
{
  const auto it = columns.find(name);
  if (it == columns.end() || it->second >= fields.size()) {
    throw std::runtime_error("Missing CSV column: " + name);
  }
  return fields[it->second];
}

std::vector<SummaryPoint> ReadSummaryCsv(const TString& path, const std::string& region)
{
  std::ifstream in(path.Data());
  if (!in) {
    throw std::runtime_error(std::string("Cannot open summary CSV: ") + path.Data());
  }

  std::string headerLine;
  if (!std::getline(in, headerLine)) {
    throw std::runtime_error(std::string("Empty summary CSV: ") + path.Data());
  }

  const auto header = SplitCsvLine(headerLine);
  std::unordered_map<std::string, size_t> columns;
  for (size_t i = 0; i < header.size(); ++i) {
    columns[header[i]] = i;
  }

  std::vector<SummaryPoint> points;
  std::string line;
  while (std::getline(in, line)) {
    if (line.empty()) continue;
    const auto fields = SplitCsvLine(line);
    const std::string rowRegion = ParseString(fields, columns, "region");
    if (rowRegion != region) continue;

    SummaryPoint point;
    point.energyMev = ParseDouble(fields, columns, "energy_mev");
    point.label = ParseString(fields, columns, "label");
    point.region = rowRegion;
    point.meanEffPercent = ParseDouble(fields, columns, "mean_collection_efficiency_percent");
    point.rmsOverMeanPercent =
      ParseDouble(fields, columns, "rms_over_mean_collection_efficiency_percent");
    point.detectedPerEvent = ParseDouble(fields, columns, "mean_detected_photons_per_event");
    point.scintillationPerEvent =
      ParseDouble(fields, columns, "mean_scintillation_photons_per_event");
    points.push_back(point);
  }

  std::sort(points.begin(), points.end(), [](const SummaryPoint& lhs, const SummaryPoint& rhs) {
    return lhs.energyMev < rhs.energyMev;
  });
  return points;
}

double MetricValue(const SummaryPoint& point, Metric metric)
{
  switch (metric) {
    case Metric::kMeanEfficiencyPercent:
      return point.meanEffPercent;
    case Metric::kRelativeNonUniformityPercent:
      return point.rmsOverMeanPercent;
    case Metric::kDetectedPerEvent:
      return point.detectedPerEvent;
    case Metric::kScintillationPerEvent:
      return point.scintillationPerEvent;
  }
  return std::numeric_limits<double>::quiet_NaN();
}

Range MetricRange(const std::vector<ThicknessRun>& runs, Metric metric)
{
  Range range;
  for (const auto& run : runs) {
    for (const auto& point : run.points) {
      range.Add(MetricValue(point, metric));
    }
  }

  if (!range.Valid()) return range;
  const double span = range.max - range.min;
  const double padding = span > 0.0 ? span * 0.12 : std::max(std::abs(range.max) * 0.05, 1.0);
  range.min -= padding;
  range.max += padding;
  if (range.min > 0.0 && metric != Metric::kMeanEfficiencyPercent) {
    range.min = std::max(0.0, range.min);
  }
  return range;
}

TGraph* MakeGraph(const ThicknessRun& run, Metric metric, int color, int markerStyle)
{
  std::vector<double> x;
  std::vector<double> y;
  for (const auto& point : run.points) {
    const double value = MetricValue(point, metric);
    if (!std::isfinite(point.energyMev) || !std::isfinite(value)) continue;
    x.push_back(point.energyMev);
    y.push_back(value);
  }

  auto* graph = new TGraph(static_cast<int>(x.size()), x.data(), y.data());
  graph->SetLineColor(color);
  graph->SetMarkerColor(color);
  graph->SetMarkerStyle(markerStyle);
  graph->SetMarkerSize(1.25);
  graph->SetLineWidth(3);
  return graph;
}

void DrawComparisonPad(const std::vector<ThicknessRun>& runs,
                       Metric metric,
                       const char* title,
                       const char* yTitle,
                       bool drawLegend)
{
  gPad->SetGrid(1, 1);
  gPad->SetLeftMargin(0.13);
  gPad->SetRightMargin(0.04);
  gPad->SetBottomMargin(0.12);
  gPad->SetTopMargin(0.10);

  auto* multi = new TMultiGraph();
  std::vector<TGraph*> graphs;
  const int colors[] = {kBlue + 1, kOrange + 7};
  const int markers[] = {20, 24};

  for (size_t i = 0; i < runs.size(); ++i) {
    auto* graph = MakeGraph(runs[i], metric, colors[i % 2], markers[i % 2]);
    graphs.push_back(graph);
    multi->Add(graph, "LP");
  }

  const auto range = MetricRange(runs, metric);
  if (range.Valid()) {
    multi->SetMinimum(range.min);
    multi->SetMaximum(range.max);
  }

  multi->SetTitle(Form("%s;primary energy [MeV];%s", title, yTitle));
  multi->Draw("A");
  multi->GetXaxis()->SetLimits(0.0, 5.5);
  multi->GetXaxis()->SetTitleSize(0.045);
  multi->GetYaxis()->SetTitleSize(0.045);
  multi->GetXaxis()->SetLabelSize(0.040);
  multi->GetYaxis()->SetLabelSize(0.040);

  if (drawLegend) {
    auto* legend = new TLegend(0.64, 0.35, 0.90, 0.50);
    legend->SetBorderSize(0);
    legend->SetFillStyle(0);
    for (size_t i = 0; i < runs.size() && i < graphs.size(); ++i) {
      legend->AddEntry(graphs[i], runs[i].label.c_str(), "lp");
    }
    legend->Draw();
  }
}

void WriteComparisonCsv(const std::vector<ThicknessRun>& runs, const TString& outPath)
{
  std::ofstream out(outPath.Data());
  out << "thickness_mm,thickness_label,energy_mev,energy_label,region,"
      << "mean_collection_efficiency_percent,"
      << "rms_over_mean_collection_efficiency_percent,"
      << "mean_detected_photons_per_event,"
      << "mean_scintillation_photons_per_event\n";
  out << std::setprecision(12);
  for (const auto& run : runs) {
    for (const auto& point : run.points) {
      out << run.thicknessMm << ','
          << run.label << ','
          << point.energyMev << ','
          << point.label << ','
          << point.region << ','
          << point.meanEffPercent << ','
          << point.rmsOverMeanPercent << ','
          << point.detectedPerEvent << ','
          << point.scintillationPerEvent << '\n';
    }
  }
}

void SaveComparisonCanvas(TCanvas* canvas, const TString& outDir, const TString& stem)
{
  canvas->SaveAs(Form("%s/%s.png", outDir.Data(), stem.Data()));
  canvas->SaveAs(Form("%s/%s.pdf", outDir.Data(), stem.Data()));
}

}  // namespace

// Usage from test/OpNovice2:
//   root -b -q 'week9_run/energy_variant/plot_week9_energy_thickness_comparison.C()'
void plot_week9_energy_thickness_comparison(
  const char* twoMmSummaryArg = "week9_run/energy_variant/root_plots/root_energy_variant_summary.csv",
  const char* fiveMmSummaryArg =
    "week9_run/energy_variant/root_plots_5mm/root_energy_variant_summary.csv",
  const char* outDirArg = "week9_run/energy_variant/root_plots_compare",
  const char* regionArg = "fiducial")
{
  gROOT->SetBatch(true);
  gStyle->SetOptStat(0);

  TString outDir(outDirArg);
  if (outDir.EndsWith("/")) outDir.Chop();
  gSystem->mkdir(outDir.Data(), true);

  std::vector<ThicknessRun> runs;
  try {
    runs.push_back({2.0, "2 mm", ReadSummaryCsv(twoMmSummaryArg, regionArg)});
    runs.push_back({5.0, "5 mm", ReadSummaryCsv(fiveMmSummaryArg, regionArg)});
  }
  catch (const std::exception& exc) {
    std::cerr << exc.what() << std::endl;
    return;
  }

  auto* canvas = new TCanvas("c_root_energy_thickness_comparison",
                             "Week 9 energy variant: 2 mm vs 5 mm",
                             1500,
                             1100);
  canvas->Divide(2, 2, 0.01, 0.01);

  canvas->cd(1);
  DrawComparisonPad(runs,
                    Metric::kMeanEfficiencyPercent,
                    "Mean collection efficiency",
                    "collection efficiency [%]",
                    true);
  canvas->cd(2);
  DrawComparisonPad(runs,
                    Metric::kRelativeNonUniformityPercent,
                    "Spatial non-uniformity",
                    "RMS / mean collection efficiency [%]",
                    false);
  canvas->cd(3);
  DrawComparisonPad(runs,
                    Metric::kDetectedPerEvent,
                    "Mean detected light yield",
                    "detected photons / event",
                    false);
  canvas->cd(4);
  DrawComparisonPad(runs,
                    Metric::kScintillationPerEvent,
                    "Mean scintillation photons, Edep proxy",
                    "scintillation photons / event",
                    false);

  const TString stem = Form("root_energy_thickness_comparison_%s", regionArg);
  SaveComparisonCanvas(canvas, outDir, stem);

  const TString summaryCsv = Form("%s/%s_summary.csv", outDir.Data(), stem.Data());
  WriteComparisonCsv(runs, summaryCsv);

  std::cout << "Wrote Week 9 energy thickness comparison to " << outDir << std::endl;
  std::cout << "Wrote comparison summary CSV to " << summaryCsv << std::endl;
}

#endif
