#ifndef OPNOVICE2_PLOT_EFFICIENCY_MAP_C
#define OPNOVICE2_PLOT_EFFICIENCY_MAP_C

#include <TBox.h>
#include <TCanvas.h>
#include <TH2D.h>
#include <TString.h>
#include <TStyle.h>
#include <TSystem.h>

#include <algorithm>
#include <cmath>
#include <cctype>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();

struct EfficiencyPoint {
  std::string tag;
  double x = kNaN;
  double y = kNaN;
  double z = kNaN;
  std::string unit;
  double xMm = kNaN;
  double yMm = kNaN;
  double events = kNaN;
  double generated = kNaN;
  double scintillation = kNaN;
  double detected = kNaN;
  double collectionEfficiency = kNaN;
  double hitPositionEvents = kNaN;
  double hitZMm = kNaN;
  double scintCentroidZMm = kNaN;
};

struct RunningStats {
  int n = 0;
  double sum = 0.0;
  double sum2 = 0.0;

  void Add(double value)
  {
    if (!std::isfinite(value)) return;
    ++n;
    sum += value;
    sum2 += value * value;
  }

  double Mean() const
  {
    return n > 0 ? sum / n : kNaN;
  }

  double Rms() const
  {
    if (n == 0) return kNaN;
    const double mean = Mean();
    return std::sqrt(std::max(0.0, sum2 / n - mean * mean));
  }
};

struct RegionStats {
  std::string region;
  int nPoints = 0;
  RunningStats collectionEfficiency;
  RunningStats detectedPerEvent;
  RunningStats generatedPerEvent;
  RunningStats scintillationPerEvent;
  RunningStats hitZMm;
  RunningStats scintCentroidZMm;
  int zeroGeneratedPoints = 0;
  int missingHitPoints = 0;
};

std::string Trim(const std::string& value)
{
  const auto begin = std::find_if_not(value.begin(), value.end(), [](unsigned char c) {
    return std::isspace(c);
  });
  const auto end = std::find_if_not(value.rbegin(), value.rend(), [](unsigned char c) {
    return std::isspace(c);
  }).base();
  if (begin >= end) return "";
  return std::string(begin, end);
}

std::vector<std::string> SplitCsvLine(const std::string& line)
{
  std::vector<std::string> fields;
  std::string field;
  bool inQuote = false;

  for (size_t i = 0; i < line.size(); ++i) {
    const char c = line[i];
    if (c == '"') {
      if (inQuote && i + 1 < line.size() && line[i + 1] == '"') {
        field.push_back('"');
        ++i;
      }
      else {
        inQuote = !inQuote;
      }
    }
    else if (c == ',' && !inQuote) {
      fields.push_back(field);
      field.clear();
    }
    else {
      field.push_back(c);
    }
  }
  fields.push_back(field);
  return fields;
}

double ParseDouble(const std::string& text)
{
  const std::string trimmed = Trim(text);
  if (trimmed.empty()) return kNaN;
  try {
    size_t parsed = 0;
    const double value = std::stod(trimmed, &parsed);
    if (parsed == 0) return kNaN;
    return value;
  }
  catch (...) {
    return kNaN;
  }
}

double UnitToMm(double value, const std::string& unit)
{
  if (unit == "mm") return value;
  if (unit == "cm") return value * 10.0;
  if (unit == "m") return value * 1000.0;
  throw std::runtime_error("Unsupported scan coordinate unit: " + unit);
}

int ColumnIndex(const std::map<std::string, int>& columns, const std::string& name)
{
  const auto found = columns.find(name);
  if (found == columns.end()) {
    throw std::runtime_error("efficiency_map.csv is missing required column: " + name);
  }
  return found->second;
}

std::vector<EfficiencyPoint> ReadEfficiencyMap(const TString& path)
{
  std::ifstream in(path.Data());
  if (!in) {
    throw std::runtime_error(std::string("Cannot open efficiency map: ") + path.Data());
  }

  std::string headerLine;
  if (!std::getline(in, headerLine)) {
    throw std::runtime_error(std::string("Empty efficiency map: ") + path.Data());
  }

  const auto header = SplitCsvLine(headerLine);
  std::map<std::string, int> columns;
  for (size_t i = 0; i < header.size(); ++i) {
    columns[Trim(header[i])] = static_cast<int>(i);
  }

  const int tagCol = ColumnIndex(columns, "tag");
  const int xCol = ColumnIndex(columns, "x");
  const int yCol = ColumnIndex(columns, "y");
  const int zCol = ColumnIndex(columns, "z");
  const int unitCol = ColumnIndex(columns, "unit");
  const int eventsCol = ColumnIndex(columns, "events");
  const int generatedCol = ColumnIndex(columns, "generated_optical_photons");
  const int scintCol = ColumnIndex(columns, "scintillation_photons");
  const int detectedCol = ColumnIndex(columns, "sipm_detected_photons");
  const int effCol = ColumnIndex(columns, "collection_efficiency");
  const int hitEventsCol = ColumnIndex(columns, "hit_position_events");
  const int hitZCol = ColumnIndex(columns, "hit_z_mm");
  const int scintZCol = ColumnIndex(columns, "scint_centroid_z_mm");

  std::vector<EfficiencyPoint> points;
  std::string line;
  int lineNumber = 1;
  while (std::getline(in, line)) {
    ++lineNumber;
    if (Trim(line).empty()) continue;

    auto fields = SplitCsvLine(line);
    if (fields.size() < header.size()) fields.resize(header.size());

    EfficiencyPoint point;
    point.tag = fields[tagCol];
    point.x = ParseDouble(fields[xCol]);
    point.y = ParseDouble(fields[yCol]);
    point.z = ParseDouble(fields[zCol]);
    point.unit = Trim(fields[unitCol]);
    point.events = ParseDouble(fields[eventsCol]);
    point.generated = ParseDouble(fields[generatedCol]);
    point.scintillation = ParseDouble(fields[scintCol]);
    point.detected = ParseDouble(fields[detectedCol]);
    point.collectionEfficiency = ParseDouble(fields[effCol]);
    point.hitPositionEvents = ParseDouble(fields[hitEventsCol]);
    point.hitZMm = ParseDouble(fields[hitZCol]);
    point.scintCentroidZMm = ParseDouble(fields[scintZCol]);

    try {
      point.xMm = UnitToMm(point.x, point.unit);
      point.yMm = UnitToMm(point.y, point.unit);
    }
    catch (const std::exception& exc) {
      throw std::runtime_error(std::string("Line ") + std::to_string(lineNumber) + ": " + exc.what());
    }

    points.push_back(point);
  }

  if (points.empty()) {
    throw std::runtime_error(std::string("No data rows in efficiency map: ") + path.Data());
  }
  return points;
}

std::vector<double> UniqueSortedValues(const std::vector<EfficiencyPoint>& points, bool useX)
{
  std::set<double> values;
  for (const auto& point : points) {
    const double value = useX ? point.xMm : point.yMm;
    if (std::isfinite(value)) values.insert(value);
  }
  return std::vector<double>(values.begin(), values.end());
}

std::vector<double> MakeBinEdges(const std::vector<double>& centers)
{
  std::vector<double> edges;
  if (centers.empty()) return edges;
  if (centers.size() == 1) {
    edges.push_back(centers.front() - 0.5);
    edges.push_back(centers.front() + 0.5);
    return edges;
  }

  edges.push_back(centers.front() - 0.5 * (centers[1] - centers[0]));
  for (size_t i = 1; i < centers.size(); ++i) {
    edges.push_back(0.5 * (centers[i - 1] + centers[i]));
  }
  edges.push_back(centers.back() + 0.5 * (centers.back() - centers[centers.size() - 2]));
  return edges;
}

double PerEvent(double total, double events)
{
  if (!std::isfinite(total) || !std::isfinite(events) || events <= 0.0) return kNaN;
  return total / events;
}

RegionStats SummarizeRegion(const std::vector<EfficiencyPoint>& points,
                             const std::string& region,
                             double fiducialLimitMm,
                             bool useFiducialMask)
{
  RegionStats stats;
  stats.region = region;

  for (const auto& point : points) {
    if (useFiducialMask &&
        (std::abs(point.xMm) > fiducialLimitMm || std::abs(point.yMm) > fiducialLimitMm)) {
      continue;
    }

    ++stats.nPoints;
    stats.collectionEfficiency.Add(point.collectionEfficiency);
    stats.detectedPerEvent.Add(PerEvent(point.detected, point.events));
    stats.generatedPerEvent.Add(PerEvent(point.generated, point.events));
    stats.scintillationPerEvent.Add(PerEvent(point.scintillation, point.events));
    stats.hitZMm.Add(point.hitZMm);
    stats.scintCentroidZMm.Add(point.scintCentroidZMm);

    if (std::isfinite(point.generated) && point.generated <= 0.0) {
      ++stats.zeroGeneratedPoints;
    }
    if (std::isfinite(point.hitPositionEvents) &&
        std::isfinite(point.events) &&
        point.hitPositionEvents < point.events) {
      ++stats.missingHitPoints;
    }
  }

  return stats;
}

void PrintStats(const RegionStats& stats)
{
  const double meanEff = stats.collectionEfficiency.Mean();
  const double rmsEff = stats.collectionEfficiency.Rms();
  const double rmsOverMean = (std::isfinite(meanEff) && meanEff != 0.0) ? rmsEff / meanEff : kNaN;

  std::cout << std::left << std::setw(10) << stats.region
            << std::right << std::setw(8) << stats.nPoints
            << std::setw(16) << meanEff
            << std::setw(16) << rmsEff
            << std::setw(16) << rmsOverMean
            << std::setw(16) << stats.detectedPerEvent.Mean()
            << std::setw(16) << stats.generatedPerEvent.Mean()
            << std::setw(16) << stats.scintillationPerEvent.Mean()
            << std::setw(12) << stats.zeroGeneratedPoints
            << std::setw(12) << stats.missingHitPoints
            << std::setw(16) << stats.hitZMm.Mean()
            << std::setw(20) << stats.scintCentroidZMm.Mean()
            << std::endl;
}

void SaveCanvas(TCanvas* canvas, const TString& outDir, const TString& stem)
{
  canvas->SaveAs(Form("%s/%s.png", outDir.Data(), stem.Data()));
  canvas->SaveAs(Form("%s/%s.pdf", outDir.Data(), stem.Data()));
}

void DrawFiducialBox(double fiducialLimitMm)
{
  if (!std::isfinite(fiducialLimitMm) || fiducialLimitMm <= 0.0) return;
  auto* box = new TBox(-fiducialLimitMm, -fiducialLimitMm, fiducialLimitMm, fiducialLimitMm);
  box->SetFillStyle(0);
  box->SetLineColor(kGray + 2);
  box->SetLineStyle(2);
  box->SetLineWidth(2);
  box->Draw("same");
}

template <typename ValueGetter>
TH2D* MakeMap(const char* name,
              const char* title,
              const std::vector<EfficiencyPoint>& points,
              const std::vector<double>& xEdges,
              const std::vector<double>& yEdges,
              ValueGetter valueGetter)
{
  auto* hist = new TH2D(
    name,
    title,
    static_cast<int>(xEdges.size() - 1), xEdges.data(),
    static_cast<int>(yEdges.size() - 1), yEdges.data());

  for (int ix = 1; ix <= hist->GetNbinsX(); ++ix) {
    for (int iy = 1; iy <= hist->GetNbinsY(); ++iy) {
      hist->SetBinContent(ix, iy, kNaN);
    }
  }

  hist->SetContour(100);
  hist->GetXaxis()->SetTitle("scan command x [mm]");
  hist->GetYaxis()->SetTitle("scan command y [mm]");

  for (const auto& point : points) {
    const double value = valueGetter(point);
    if (!std::isfinite(value)) continue;
    const int bx = hist->GetXaxis()->FindBin(point.xMm);
    const int by = hist->GetYaxis()->FindBin(point.yMm);
    hist->SetBinContent(bx, by, value);
  }

  return hist;
}

void DrawMap(TH2D* hist, const TString& outDir, const TString& stem, double fiducialLimitMm)
{
  auto* canvas = new TCanvas(Form("c_%s", stem.Data()), hist->GetTitle(), 1050, 850);
  canvas->SetLeftMargin(0.12);
  canvas->SetRightMargin(0.18);
  canvas->SetBottomMargin(0.12);
  canvas->SetTopMargin(0.10);

  hist->GetZaxis()->SetTitleOffset(1.30);
  hist->GetZaxis()->SetTitleSize(0.040);
  hist->GetZaxis()->SetLabelSize(0.038);
  hist->GetXaxis()->SetTitleSize(0.045);
  hist->GetYaxis()->SetTitleSize(0.045);
  hist->GetXaxis()->SetLabelSize(0.040);
  hist->GetYaxis()->SetLabelSize(0.040);
  hist->Draw("COLZ");
  DrawFiducialBox(fiducialLimitMm);
  SaveCanvas(canvas, outDir, stem);
}

TString DefaultOutDir(const TString& runDir, const char* outDirArg)
{
  TString outDir(outDirArg ? outDirArg : "");
  if (outDir.IsNull()) outDir = runDir;
  if (outDir.EndsWith("/")) outDir.Chop();
  gSystem->mkdir(outDir.Data(), true);
  return outDir;
}

}  // namespace

// Usage from test/OpNovice2:
//   root -b -q 'plot_efficiency_map.C("scan_runs/<run_id>")'
//   root -b -q 'plot_efficiency_map.C("scan_runs/<run_id>",45.0,"week6_analysis/root_plots/<run_id>")'
void plot_efficiency_map(const char* runDirArg = "scan_latest",
                         double fiducialLimitMm = 45.0,
                         const char* outDirArg = "")
{
  gStyle->SetOptStat(0);

  TString runDir(runDirArg);
  if (runDir.EndsWith("/")) runDir.Chop();

  const TString outDir = DefaultOutDir(runDir, outDirArg);
  const TString csvPath = Form("%s/efficiency_map.csv", runDir.Data());

  std::vector<EfficiencyPoint> points;
  try {
    points = ReadEfficiencyMap(csvPath);
  }
  catch (const std::exception& exc) {
    std::cerr << exc.what() << std::endl;
    return;
  }

  const auto xs = UniqueSortedValues(points, true);
  const auto ys = UniqueSortedValues(points, false);
  if (xs.empty() || ys.empty()) {
    std::cerr << "No finite scan coordinates found in " << csvPath << std::endl;
    return;
  }

  const auto xEdges = MakeBinEdges(xs);
  const auto yEdges = MakeBinEdges(ys);

  auto* collectionMap = MakeMap(
    "h_collection_efficiency",
    "Collection efficiency;scan command x [mm];scan command y [mm];collection efficiency",
    points,
    xEdges,
    yEdges,
    [](const EfficiencyPoint& point) { return point.collectionEfficiency; });

  auto* detectedMap = MakeMap(
    "h_detected_per_event",
    "Detected optical photons per event;scan command x [mm];scan command y [mm];detected photons / event",
    points,
    xEdges,
    yEdges,
    [](const EfficiencyPoint& point) { return PerEvent(point.detected, point.events); });

  auto* generatedMap = MakeMap(
    "h_generated_per_event",
    "Generated optical photons per event;scan command x [mm];scan command y [mm];generated photons / event",
    points,
    xEdges,
    yEdges,
    [](const EfficiencyPoint& point) { return PerEvent(point.generated, point.events); });

  auto* hitFractionMap = MakeMap(
    "h_hit_fraction",
    "Primary hit fraction;scan command x [mm];scan command y [mm];hit events / events",
    points,
    xEdges,
    yEdges,
    [](const EfficiencyPoint& point) { return PerEvent(point.hitPositionEvents, point.events); });

  auto* hitZMap = MakeMap(
    "h_hit_z",
    "Mean primary hit z;scan command x [mm];scan command y [mm];hit z [mm]",
    points,
    xEdges,
    yEdges,
    [](const EfficiencyPoint& point) { return point.hitZMm; });

  auto* scintZMap = MakeMap(
    "h_scint_centroid_z",
    "Mean scintillation centroid z;scan command x [mm];scan command y [mm];scintillation centroid z [mm]",
    points,
    xEdges,
    yEdges,
    [](const EfficiencyPoint& point) { return point.scintCentroidZMm; });

  DrawMap(collectionMap, outDir, "root_collection_efficiency_map", fiducialLimitMm);
  DrawMap(detectedMap, outDir, "root_detected_yield_map", fiducialLimitMm);
  DrawMap(generatedMap, outDir, "root_generated_yield_map", fiducialLimitMm);
  DrawMap(hitFractionMap, outDir, "root_hit_fraction_map", fiducialLimitMm);
  DrawMap(hitZMap, outDir, "root_hit_z_map", fiducialLimitMm);
  DrawMap(scintZMap, outDir, "root_scint_centroid_z_map", fiducialLimitMm);

  const auto full = SummarizeRegion(points, "full", fiducialLimitMm, false);
  const auto fiducial = SummarizeRegion(points, "fiducial", fiducialLimitMm, true);

  std::cout << "Read " << points.size() << " points from " << csvPath << std::endl;
  std::cout << "Wrote ROOT-style plots to " << outDir << std::endl;
  std::cout << std::left << std::setw(10) << "region"
            << std::right << std::setw(8) << "points"
            << std::setw(16) << "mean_eff"
            << std::setw(16) << "rms_eff"
            << std::setw(16) << "rms/mean"
            << std::setw(16) << "det/event"
            << std::setw(16) << "gen/event"
            << std::setw(16) << "scint/event"
            << std::setw(12) << "zero_gen"
            << std::setw(12) << "miss_hit"
            << std::setw(16) << "mean_hit_z"
            << std::setw(20) << "mean_scint_z"
            << std::endl;
  PrintStats(full);
  PrintStats(fiducial);
}

#endif
