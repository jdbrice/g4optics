#ifndef OPNOVICE2_PLOT_WEEK9_ENERGY_VARIANT_C
#define OPNOVICE2_PLOT_WEEK9_ENERGY_VARIANT_C

#include "../../plot_efficiency_map.C"

#include <TAxis.h>
#include <TGraph.h>
#include <TLegend.h>
#include <TMultiGraph.h>
#include <TObject.h>
#include <TPad.h>
#include <TROOT.h>
#include <TSystem.h>

#include <fstream>

namespace {

struct EnergySpec {
  TString dir;
  double energyMev = 0.0;
  TString label;
};

struct EnergyRun {
  EnergySpec spec;
  std::vector<EfficiencyPoint> points;
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

struct SummaryRow {
  double energyMev = kNaN;
  std::string label;
  std::string region;
  int nPoints = 0;
  double meanEff = kNaN;
  double rmsEff = kNaN;
  double rmsOverMeanEff = kNaN;
  double meanDetectedPerEvent = kNaN;
  double rmsDetectedPerEvent = kNaN;
  double meanGeneratedPerEvent = kNaN;
  double meanScintillationPerEvent = kNaN;
  double minEff = kNaN;
  double maxEff = kNaN;
  double minDetectedPerEvent = kNaN;
  double maxDetectedPerEvent = kNaN;
  int zeroGeneratedPoints = 0;
  int missingHitPoints = 0;
};

enum class MapMetric {
  kCollectionEfficiency,
  kDetectedPerEvent,
  kGeneratedPerEvent,
  kScintillationPerEvent
};

enum class TrendMetric {
  kMeanCollectionEfficiencyPercent,
  kRelativeNonUniformityPercent,
  kDetectedPerEvent,
  kScintillationPerEvent
};

TString JoinPath(TString base, const TString& child)
{
  if (base.EndsWith("/")) base.Chop();
  return Form("%s/%s", base.Data(), child.Data());
}

std::vector<EnergySpec> DefaultEnergySpecs()
{
  return {
    {"0p5_MeV", 0.5, "0.5 MeV"},
    {"1_MeV", 1.0, "1 MeV"},
    {"2_MeV", 2.0, "2 MeV"},
    {"5_MeV", 5.0, "5 MeV"},
  };
}

void DeleteRootObjectIfPresent(const char* name)
{
  TObject* object = gROOT->FindObject(name);
  if (object) delete object;
}

void ClearSingleEnergyPlotObjects()
{
  const char* canvasNames[] = {
    "c_root_collection_efficiency_map",
    "c_root_detected_yield_map",
    "c_root_generated_yield_map",
    "c_root_hit_fraction_map",
    "c_root_hit_z_map",
    "c_root_scint_centroid_z_map",
  };
  for (const auto* name : canvasNames) {
    DeleteRootObjectIfPresent(name);
  }

  const char* histogramNames[] = {
    "h_collection_efficiency",
    "h_detected_per_event",
    "h_generated_per_event",
    "h_hit_fraction",
    "h_hit_z",
    "h_scint_centroid_z",
  };
  for (const auto* name : histogramNames) {
    DeleteRootObjectIfPresent(name);
  }
}

double MapValue(const EfficiencyPoint& point, MapMetric metric)
{
  switch (metric) {
    case MapMetric::kCollectionEfficiency:
      return point.collectionEfficiency;
    case MapMetric::kDetectedPerEvent:
      return PerEvent(point.detected, point.events);
    case MapMetric::kGeneratedPerEvent:
      return PerEvent(point.generated, point.events);
    case MapMetric::kScintillationPerEvent:
      return PerEvent(point.scintillation, point.events);
  }
  return kNaN;
}

bool PassesFiducialMask(const EfficiencyPoint& point, double fiducialLimitMm, bool useFiducialMask)
{
  if (!useFiducialMask) return true;
  return std::abs(point.xMm) <= fiducialLimitMm && std::abs(point.yMm) <= fiducialLimitMm;
}

Range MapRange(const std::vector<EnergyRun>& runs, MapMetric metric)
{
  Range range;
  for (const auto& run : runs) {
    for (const auto& point : run.points) {
      range.Add(MapValue(point, metric));
    }
  }
  if (range.Valid() && range.min == range.max) {
    range.min *= 0.95;
    range.max *= 1.05;
  }
  return range;
}

std::vector<double> SharedEdges(const std::vector<EnergyRun>& runs, bool useX)
{
  if (runs.empty()) return {};
  return MakeBinEdges(UniqueSortedValues(runs.front().points, useX));
}

TH2D* MakeEnergyMap(const EnergyRun& run,
                    const std::vector<double>& xEdges,
                    const std::vector<double>& yEdges,
                    MapMetric metric,
                    const char* name,
                    const char* zTitle)
{
  TString title = Form("%s;scan command x [mm];scan command y [mm];%s",
                       run.spec.label.Data(),
                       zTitle);
  return MakeMap(name, title.Data(), run.points, xEdges, yEdges, [metric](const EfficiencyPoint& point) {
    return MapValue(point, metric);
  });
}

SummaryRow BuildSummaryRow(const EnergyRun& run,
                           const std::string& region,
                           double fiducialLimitMm,
                           bool useFiducialMask)
{
  const auto stats = SummarizeRegion(run.points, region, fiducialLimitMm, useFiducialMask);

  Range effRange;
  Range detectedRange;
  for (const auto& point : run.points) {
    if (!PassesFiducialMask(point, fiducialLimitMm, useFiducialMask)) continue;
    effRange.Add(point.collectionEfficiency);
    detectedRange.Add(PerEvent(point.detected, point.events));
  }

  SummaryRow row;
  row.energyMev = run.spec.energyMev;
  row.label = run.spec.label.Data();
  row.region = region;
  row.nPoints = stats.nPoints;
  row.meanEff = stats.collectionEfficiency.Mean();
  row.rmsEff = stats.collectionEfficiency.Rms();
  row.rmsOverMeanEff =
    (std::isfinite(row.meanEff) && row.meanEff != 0.0) ? row.rmsEff / row.meanEff : kNaN;
  row.meanDetectedPerEvent = stats.detectedPerEvent.Mean();
  row.rmsDetectedPerEvent = stats.detectedPerEvent.Rms();
  row.meanGeneratedPerEvent = stats.generatedPerEvent.Mean();
  row.meanScintillationPerEvent = stats.scintillationPerEvent.Mean();
  row.minEff = effRange.Valid() ? effRange.min : kNaN;
  row.maxEff = effRange.Valid() ? effRange.max : kNaN;
  row.minDetectedPerEvent = detectedRange.Valid() ? detectedRange.min : kNaN;
  row.maxDetectedPerEvent = detectedRange.Valid() ? detectedRange.max : kNaN;
  row.zeroGeneratedPoints = stats.zeroGeneratedPoints;
  row.missingHitPoints = stats.missingHitPoints;
  return row;
}

std::vector<SummaryRow> BuildSummaryRows(const std::vector<EnergyRun>& runs, double fiducialLimitMm)
{
  std::vector<SummaryRow> rows;
  for (const auto& run : runs) {
    rows.push_back(BuildSummaryRow(run, "full", fiducialLimitMm, false));
    rows.push_back(BuildSummaryRow(run, "fiducial", fiducialLimitMm, true));
  }
  return rows;
}

double TrendValue(const SummaryRow& row, TrendMetric metric)
{
  switch (metric) {
    case TrendMetric::kMeanCollectionEfficiencyPercent:
      return row.meanEff * 100.0;
    case TrendMetric::kRelativeNonUniformityPercent:
      return row.rmsOverMeanEff * 100.0;
    case TrendMetric::kDetectedPerEvent:
      return row.meanDetectedPerEvent;
    case TrendMetric::kScintillationPerEvent:
      return row.meanScintillationPerEvent;
  }
  return kNaN;
}

TGraph* MakeTrendGraph(const std::vector<SummaryRow>& rows,
                       const std::string& region,
                       TrendMetric metric,
                       int color,
                       int markerStyle)
{
  std::vector<double> x;
  std::vector<double> y;
  for (const auto& row : rows) {
    if (row.region != region) continue;
    const double value = TrendValue(row, metric);
    if (!std::isfinite(row.energyMev) || !std::isfinite(value)) continue;
    x.push_back(row.energyMev);
    y.push_back(value);
  }

  auto* graph = new TGraph(static_cast<int>(x.size()), x.data(), y.data());
  graph->SetLineColor(color);
  graph->SetMarkerColor(color);
  graph->SetMarkerStyle(markerStyle);
  graph->SetMarkerSize(1.2);
  graph->SetLineWidth(2);
  return graph;
}

Range TrendRange(const std::vector<SummaryRow>& rows, TrendMetric metric)
{
  Range range;
  for (const auto& row : rows) {
    range.Add(TrendValue(row, metric));
  }
  if (!range.Valid()) return range;
  const double span = range.max - range.min;
  const double padding = span > 0.0 ? span * 0.12 : std::max(std::abs(range.max) * 0.05, 1.0);
  range.min -= padding;
  range.max += padding;
  return range;
}

void DrawTrendPad(const std::vector<SummaryRow>& rows,
                  TrendMetric metric,
                  const char* title,
                  const char* yTitle)
{
  gPad->SetGrid(1, 1);
  gPad->SetLeftMargin(0.13);
  gPad->SetRightMargin(0.04);
  gPad->SetBottomMargin(0.12);
  gPad->SetTopMargin(0.10);

  auto* graphFull = MakeTrendGraph(rows, "full", metric, kBlue + 1, 20);
  auto* graphFiducial = MakeTrendGraph(rows, "fiducial", metric, kOrange + 7, 24);

  auto* multi = new TMultiGraph();
  multi->Add(graphFull, "LP");
  multi->Add(graphFiducial, "LP");

  const auto yRange = TrendRange(rows, metric);
  if (yRange.Valid()) {
    multi->SetMinimum(yRange.min);
    multi->SetMaximum(yRange.max);
  }

  multi->SetTitle(Form("%s;primary energy [MeV];%s", title, yTitle));
  multi->Draw("A");
  multi->GetXaxis()->SetLimits(0.0, 5.5);
  multi->GetXaxis()->SetTitleSize(0.045);
  multi->GetYaxis()->SetTitleSize(0.045);
  multi->GetXaxis()->SetLabelSize(0.040);
  multi->GetYaxis()->SetLabelSize(0.040);

  auto* legend = new TLegend(0.55, 0.75, 0.90, 0.90);
  legend->SetBorderSize(0);
  legend->SetFillStyle(0);
  legend->AddEntry(graphFull, "full 21x21 grid", "lp");
  legend->AddEntry(graphFiducial, "|x|, |y| <= 45 mm", "lp");
  legend->Draw();
}

void WriteSummaryCsv(const std::vector<SummaryRow>& rows, const TString& outPath)
{
  std::ofstream out(outPath.Data());
  out << "energy_mev,label,region,n_points,"
      << "mean_collection_efficiency,mean_collection_efficiency_percent,"
      << "rms_collection_efficiency,rms_over_mean_collection_efficiency,"
      << "rms_over_mean_collection_efficiency_percent,"
      << "mean_detected_photons_per_event,rms_detected_photons_per_event,"
      << "mean_generated_photons_per_event,mean_scintillation_photons_per_event,"
      << "min_collection_efficiency,max_collection_efficiency,"
      << "min_detected_photons_per_event,max_detected_photons_per_event,"
      << "zero_generated_points,missing_hit_points\n";
  out << std::setprecision(12);
  for (const auto& row : rows) {
    out << row.energyMev << ','
        << row.label << ','
        << row.region << ','
        << row.nPoints << ','
        << row.meanEff << ','
        << row.meanEff * 100.0 << ','
        << row.rmsEff << ','
        << row.rmsOverMeanEff << ','
        << row.rmsOverMeanEff * 100.0 << ','
        << row.meanDetectedPerEvent << ','
        << row.rmsDetectedPerEvent << ','
        << row.meanGeneratedPerEvent << ','
        << row.meanScintillationPerEvent << ','
        << row.minEff << ','
        << row.maxEff << ','
        << row.minDetectedPerEvent << ','
        << row.maxDetectedPerEvent << ','
        << row.zeroGeneratedPoints << ','
        << row.missingHitPoints << '\n';
  }
}

void DrawEnergyMapPanel(const std::vector<EnergyRun>& runs,
                        MapMetric metric,
                        const char* canvasTitle,
                        const char* zTitle,
                        const TString& outDir,
                        const TString& stem,
                        double fiducialLimitMm)
{
  const auto xEdges = SharedEdges(runs, true);
  const auto yEdges = SharedEdges(runs, false);
  if (xEdges.empty() || yEdges.empty()) return;

  const auto zRange = MapRange(runs, metric);
  auto* canvas = new TCanvas(Form("c_%s", stem.Data()), canvasTitle, 1500, 1200);
  canvas->Divide(2, 2, 0.01, 0.01);

  for (size_t i = 0; i < runs.size(); ++i) {
    canvas->cd(static_cast<int>(i + 1));
    gPad->SetLeftMargin(0.11);
    gPad->SetRightMargin(0.18);
    gPad->SetBottomMargin(0.11);
    gPad->SetTopMargin(0.10);

    auto* hist = MakeEnergyMap(runs[i],
                               xEdges,
                               yEdges,
                               metric,
                               Form("h_%s_%zu", stem.Data(), i),
                               zTitle);
    if (zRange.Valid()) {
      hist->SetMinimum(zRange.min);
      hist->SetMaximum(zRange.max);
    }
    hist->SetStats(0);
    hist->SetContour(100);
    hist->GetZaxis()->SetTitleOffset(1.25);
    hist->Draw("COLZ");
    DrawFiducialBox(fiducialLimitMm);
  }

  SaveCanvas(canvas, outDir, stem);
}

void DrawSummaryTrends(const std::vector<SummaryRow>& rows, const TString& outDir)
{
  auto* canvas = new TCanvas("c_root_energy_summary_trends",
                             "Week 9 energy variant summary trends",
                             1500,
                             1100);
  canvas->Divide(2, 2, 0.01, 0.01);

  canvas->cd(1);
  DrawTrendPad(rows,
               TrendMetric::kMeanCollectionEfficiencyPercent,
               "Mean collection efficiency",
               "collection efficiency [%]");
  canvas->cd(2);
  DrawTrendPad(rows,
               TrendMetric::kRelativeNonUniformityPercent,
               "Spatial non-uniformity",
               "RMS / mean collection efficiency [%]");
  canvas->cd(3);
  DrawTrendPad(rows,
               TrendMetric::kDetectedPerEvent,
               "Mean detected light yield",
               "detected photons / event");
  canvas->cd(4);
  DrawTrendPad(rows,
               TrendMetric::kScintillationPerEvent,
               "Mean scintillation photons, Edep proxy",
               "scintillation photons / event");

  SaveCanvas(canvas, outDir, "root_energy_summary_trends");
}

}  // namespace

// Usage from test/OpNovice2:
//   root -b -q 'week9_run/energy_variant/plot_week9_energy_variant.C("week9_run/energy_variant/2mm thickness")'
void plot_week9_energy_variant(const char* baseDirArg = "week9_run/energy_variant/2mm thickness",
                               const char* outDirArg = "week9_run/energy_variant/root_plots",
                               double fiducialLimitMm = 45.0)
{
  gROOT->SetBatch(true);
  gStyle->SetOptStat(0);
  gStyle->SetPalette(kBird);

  TString baseDir(baseDirArg);
  if (baseDir.EndsWith("/")) baseDir.Chop();
  TString outDir(outDirArg);
  if (outDir.EndsWith("/")) outDir.Chop();
  gSystem->mkdir(outDir.Data(), true);

  std::vector<EnergyRun> runs;
  for (const auto& spec : DefaultEnergySpecs()) {
    const TString runDir = JoinPath(baseDir, spec.dir);
    const TString csvPath = JoinPath(runDir, "efficiency_map.csv");
    EnergyRun run;
    run.spec = spec;
    try {
      run.points = ReadEfficiencyMap(csvPath);
    }
    catch (const std::exception& exc) {
      std::cerr << exc.what() << std::endl;
      return;
    }
    runs.push_back(run);

    const TString singleOutDir = JoinPath(outDir, spec.dir);
    ClearSingleEnergyPlotObjects();
    plot_efficiency_map(runDir.Data(), fiducialLimitMm, singleOutDir.Data());
    ClearSingleEnergyPlotObjects();
  }

  const auto rows = BuildSummaryRows(runs, fiducialLimitMm);
  const TString summaryCsv = JoinPath(outDir, "root_energy_variant_summary.csv");
  WriteSummaryCsv(rows, summaryCsv);

  DrawEnergyMapPanel(runs,
                     MapMetric::kCollectionEfficiency,
                     "Week 9 energy variant collection efficiency",
                     "collection efficiency",
                     outDir,
                     "root_energy_collection_efficiency_panel",
                     fiducialLimitMm);
  DrawEnergyMapPanel(runs,
                     MapMetric::kDetectedPerEvent,
                     "Week 9 energy variant detected light yield",
                     "detected photons / event",
                     outDir,
                     "root_energy_detected_yield_panel",
                     fiducialLimitMm);
  DrawEnergyMapPanel(runs,
                     MapMetric::kScintillationPerEvent,
                     "Week 9 energy variant scintillation photons, Edep proxy",
                     "scint. photons / event (Edep proxy)",
                     outDir,
                     "root_energy_scintillation_yield_panel",
                     fiducialLimitMm);
  DrawSummaryTrends(rows, outDir);

  std::cout << "Wrote Week 9 energy ROOT plots to " << outDir << std::endl;
  std::cout << "Wrote summary CSV to " << summaryCsv << std::endl;
}

#endif
