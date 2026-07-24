#ifndef OPNOVICE2_PLOT_WEEK11_NOMINAL_PREDICTION_C
#define OPNOVICE2_PLOT_WEEK11_NOMINAL_PREDICTION_C

#include "../plot_efficiency_map.C"

#include <TLegend.h>
#include <TROOT.h>
#include <TSystem.h>

#include <fstream>
#include <map>

namespace {

struct RunSpec {
  TString label;
  TString dir;
  double fiducialLimitMm = 45.0;
};

struct RunData {
  RunSpec spec;
  std::vector<EfficiencyPoint> points;
};

struct ValueRange {
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

  void Pad(double fraction = 0.06)
  {
    if (!Valid()) return;
    const double span = max - min;
    const double pad = span > 0.0 ? span * fraction : std::max(std::abs(max) * fraction, 1.e-6);
    min -= pad;
    max += pad;
  }
};

struct SummaryRow {
  TString label;
  TString scope;
  int n = 0;
  double meanEff = kNaN;
  double rmsEff = kNaN;
  double rmsOverMean = kNaN;
  double detPerEvent = kNaN;
  double scintPerEvent = kNaN;
  double hitEventsPerPoint = kNaN;
  double primaryEnergyMeanMeV = kNaN;
};

enum class Metric {
  kCollectionEfficiency,
  kDetectedPerEvent,
  kScintillationPerEvent,
};

TString JoinPath(TString base, const TString& child)
{
  if (base.EndsWith("/")) base.Chop();
  return Form("%s/%s", base.Data(), child.Data());
}

double MetricValue(const EfficiencyPoint& point, Metric metric)
{
  switch (metric) {
    case Metric::kCollectionEfficiency:
      return point.collectionEfficiency;
    case Metric::kDetectedPerEvent:
      return PerEvent(point.detected, point.events);
    case Metric::kScintillationPerEvent:
      return PerEvent(point.scintillation, point.events);
  }
  return kNaN;
}

const char* MetricZTitle(Metric metric)
{
  switch (metric) {
    case Metric::kCollectionEfficiency:
      return "collection efficiency";
    case Metric::kDetectedPerEvent:
      return "detected photons / event";
    case Metric::kScintillationPerEvent:
      return "scintillation photons / event";
  }
  return "";
}

int CoordKey(double valueMm)
{
  return static_cast<int>(std::llround(valueMm * 1000.0));
}

std::map<std::pair<int, int>, const EfficiencyPoint*> PointIndex(const std::vector<EfficiencyPoint>& points)
{
  std::map<std::pair<int, int>, const EfficiencyPoint*> index;
  for (const auto& point : points) {
    index[{CoordKey(point.xMm), CoordKey(point.yMm)}] = &point;
  }
  return index;
}

ValueRange SharedRange(const std::vector<RunData>& runs, Metric metric)
{
  ValueRange range;
  for (const auto& run : runs) {
    for (const auto& point : run.points) range.Add(MetricValue(point, metric));
  }
  range.Pad();
  return range;
}

ValueRange RatioRange(const RunData& numerator, const RunData& denominator, Metric metric)
{
  const auto denominatorIndex = PointIndex(denominator.points);
  ValueRange range;
  for (const auto& point : numerator.points) {
    const auto found = denominatorIndex.find({CoordKey(point.xMm), CoordKey(point.yMm)});
    if (found == denominatorIndex.end()) continue;
    const double num = MetricValue(point, metric);
    const double den = MetricValue(*found->second, metric);
    if (std::isfinite(num) && std::isfinite(den) && den != 0.0) range.Add(num / den);
  }
  range.Pad(0.08);
  return range;
}

TH2D* MakeMetricMap(const RunData& run, Metric metric, const char* name, const char* title)
{
  const auto xs = UniqueSortedValues(run.points, true);
  const auto ys = UniqueSortedValues(run.points, false);
  const auto xEdges = MakeBinEdges(xs);
  const auto yEdges = MakeBinEdges(ys);
  return MakeMap(name,
                 Form("%s;scan command x [mm];scan command y [mm];%s", title, MetricZTitle(metric)),
                 run.points,
                 xEdges,
                 yEdges,
                 [metric](const EfficiencyPoint& point) { return MetricValue(point, metric); });
}

TH2D* MakeRatioMap(const RunData& numerator,
                   const RunData& denominator,
                   Metric metric,
                   const char* name,
                   const char* title)
{
  const auto xs = UniqueSortedValues(numerator.points, true);
  const auto ys = UniqueSortedValues(numerator.points, false);
  const auto xEdges = MakeBinEdges(xs);
  const auto yEdges = MakeBinEdges(ys);
  const auto denominatorIndex = PointIndex(denominator.points);
  return MakeMap(name,
                 Form("%s;scan command x [mm];scan command y [mm];ratio", title),
                 numerator.points,
                 xEdges,
                 yEdges,
                 [&](const EfficiencyPoint& point) {
                   const auto found = denominatorIndex.find({CoordKey(point.xMm), CoordKey(point.yMm)});
                   if (found == denominatorIndex.end()) return kNaN;
                   const double num = MetricValue(point, metric);
                   const double den = MetricValue(*found->second, metric);
                   if (!std::isfinite(num) || !std::isfinite(den) || den == 0.0) return kNaN;
                   return num / den;
                 });
}

void DrawPanelMap(TH2D* hist, double fiducialLimitMm, const ValueRange& range)
{
  gPad->SetLeftMargin(0.11);
  gPad->SetRightMargin(0.18);
  gPad->SetBottomMargin(0.12);
  gPad->SetTopMargin(0.10);
  if (range.Valid()) {
    hist->SetMinimum(range.min);
    hist->SetMaximum(range.max);
  }
  hist->SetStats(0);
  hist->SetContour(100);
  hist->GetZaxis()->SetTitleOffset(1.28);
  hist->Draw("COLZ");
  DrawFiducialBox(fiducialLimitMm);
}

void DrawMetricPanel(const std::vector<RunData>& runs,
                     Metric metric,
                     const TString& canvasTitle,
                     const TString& outDir,
                     const TString& stem)
{
  const auto range = SharedRange(runs, metric);
  auto* canvas = new TCanvas(Form("c_%s", stem.Data()), canvasTitle, 1900, 650);
  canvas->Divide(static_cast<int>(runs.size()), 1, 0.01, 0.01);
  for (size_t i = 0; i < runs.size(); ++i) {
    canvas->cd(static_cast<int>(i) + 1);
    auto* hist = MakeMetricMap(runs[i],
                               metric,
                               Form("h_%s_%zu", stem.Data(), i),
                               runs[i].spec.label.Data());
    DrawPanelMap(hist, runs[i].spec.fiducialLimitMm, range);
  }
  SaveCanvas(canvas, outDir, stem);
}

void DrawRatioPanel(const RunData& denominator,
                    const std::vector<RunData>& numerators,
                    Metric metric,
                    const TString& canvasTitle,
                    const TString& outDir,
                    const TString& stem)
{
  ValueRange range;
  for (const auto& run : numerators) {
    const auto local = RatioRange(run, denominator, metric);
    range.Add(local.min);
    range.Add(local.max);
  }
  range.Pad(0.03);

  auto* canvas = new TCanvas(Form("c_%s", stem.Data()), canvasTitle, 1350, 650);
  canvas->Divide(static_cast<int>(numerators.size()), 1, 0.01, 0.01);
  for (size_t i = 0; i < numerators.size(); ++i) {
    canvas->cd(static_cast<int>(i) + 1);
    auto* hist = MakeRatioMap(numerators[i],
                              denominator,
                              metric,
                              Form("h_%s_%zu", stem.Data(), i),
                              Form("%s / back", numerators[i].spec.label.Data()));
    DrawPanelMap(hist, numerators[i].spec.fiducialLimitMm, range);
  }
  SaveCanvas(canvas, outDir, stem);
}

SummaryRow BuildSummary(const RunData& run, const TString& scope, bool fiducial)
{
  const auto stats = SummarizeRegion(run.points, scope.Data(), run.spec.fiducialLimitMm, fiducial);
  SummaryRow row;
  row.label = run.spec.label;
  row.scope = scope;
  row.n = stats.nPoints;
  row.meanEff = stats.collectionEfficiency.Mean();
  row.rmsEff = stats.collectionEfficiency.Rms();
  row.rmsOverMean = std::isfinite(row.meanEff) && row.meanEff != 0.0 ? row.rmsEff / row.meanEff : kNaN;
  row.detPerEvent = stats.detectedPerEvent.Mean();
  row.scintPerEvent = stats.scintillationPerEvent.Mean();

  RunningStats hitEvents;
  RunningStats primaryEnergy;
  for (const auto& point : run.points) {
    if (fiducial &&
        (std::abs(point.xMm) > run.spec.fiducialLimitMm ||
         std::abs(point.yMm) > run.spec.fiducialLimitMm)) {
      continue;
    }
    hitEvents.Add(point.hitPositionEvents);
  }
  row.hitEventsPerPoint = hitEvents.Mean();
  return row;
}

void WriteSummaryCsv(const std::vector<SummaryRow>& rows, const TString& outPath)
{
  std::ofstream out(outPath.Data());
  out << "label,scope,n_points,mean_eff,rms_eff,rms_over_mean,detected_per_event,"
      << "scintillation_per_event,hit_events_per_point\n";
  out << std::setprecision(12);
  for (const auto& row : rows) {
    out << row.label << ','
        << row.scope << ','
        << row.n << ','
        << row.meanEff << ','
        << row.rmsEff << ','
        << row.rmsOverMean << ','
        << row.detPerEvent << ','
        << row.scintPerEvent << ','
        << row.hitEventsPerPoint << '\n';
  }
}

void PrintCoreSummary(const std::vector<SummaryRow>& rows)
{
  std::cout << std::setprecision(8);
  for (const auto& row : rows) {
    std::cout << std::left << std::setw(30) << (row.label + " " + row.scope)
              << "mean_eff = " << row.meanEff
              << ", rms/mean = " << row.rmsOverMean
              << ", det/event = " << row.detPerEvent
              << ", scint/event = " << row.scintPerEvent
              << std::endl;
  }
}

void DrawSummaryBars(const std::vector<SummaryRow>& rows, const TString& outDir)
{
  std::vector<SummaryRow> fidRows;
  for (const auto& row : rows) {
    if (row.scope == "fiducial") fidRows.push_back(row);
  }

  const char* titles[] = {
    "Mean collection efficiency",
    "Spatial non-uniformity",
    "Detected light yield",
  };
  const char* yTitles[] = {
    "collection efficiency [%]",
    "RMS / mean [%]",
    "detected photons / event",
  };

  auto* canvas = new TCanvas("c_week11_summary_bars",
                             "Week 11 nominal prediction fiducial summary",
                             1600,
                             1150);
  canvas->Divide(1, 3, 0.01, 0.01);
  for (int metric = 0; metric < 3; ++metric) {
    canvas->cd(metric + 1);
    gPad->SetLeftMargin(0.10);
    gPad->SetRightMargin(0.03);
    gPad->SetBottomMargin(0.25);
    gPad->SetTopMargin(0.10);
    gPad->SetGrid(0, 1);

    auto* hist = new TH1D(Form("h_week11_summary_%d", metric),
                          Form("%s;;%s", titles[metric], yTitles[metric]),
                          static_cast<int>(fidRows.size()),
                          0.5,
                          fidRows.size() + 0.5);
    double maxValue = 0.0;
    for (size_t i = 0; i < fidRows.size(); ++i) {
      double value = kNaN;
      if (metric == 0) value = fidRows[i].meanEff * 100.0;
      if (metric == 1) value = fidRows[i].rmsOverMean * 100.0;
      if (metric == 2) value = fidRows[i].detPerEvent;
      hist->SetBinContent(static_cast<int>(i) + 1, value);
      TString label = fidRows[i].label;
      label.ReplaceAll(" back+dimple", " dimple");
      label.ReplaceAll("100x100x24 ", "100x100x24\n");
      label.ReplaceAll("100x100x16 ", "100x100x16\n");
      label.ReplaceAll("50x50x16 ", "50x50x16\n");
      hist->GetXaxis()->SetBinLabel(static_cast<int>(i) + 1, label.Data());
      if (std::isfinite(value)) maxValue = std::max(maxValue, value);
    }
    hist->SetMaximum(maxValue * 1.20);
    hist->SetFillColor(kAzure + 1);
    hist->SetLineColor(kAzure + 3);
    hist->SetStats(0);
    hist->GetXaxis()->SetLabelSize(0.060);
    hist->LabelsOption("h", "X");
    hist->Draw("bar2");
  }
  SaveCanvas(canvas, outDir, "root_week11_nominal_prediction_fiducial_summary");
}

}  // namespace

void plot_week11_nominal_prediction(const char* baseDirArg = "week11_run",
                                    const char* outDirArg = "week11_run/root_plots")
{
  gROOT->SetBatch(true);
  gStyle->SetOptStat(0);
  gStyle->SetPalette(kBird);

  TString baseDir(baseDirArg);
  if (baseDir.EndsWith("/")) baseDir.Chop();
  TString outDir(outDirArg);
  if (outDir.EndsWith("/")) outDir.Chop();
  gSystem->mkdir(outDir.Data(), true);

  std::vector<RunData> all = {
    {{"50x50x16 back", JoinPath(baseDir, "week11_nominal_50x50x16_center_back"), 20.0}, {}},
    {{"100x100x16 back", JoinPath(baseDir, "week11_nominal_100x100x16_center_back"), 45.0}, {}},
    {{"100x100x24 back", JoinPath(baseDir, "week11_nominal_100x100x24_center_back"), 45.0}, {}},
    {{"100x100x24 side", JoinPath(baseDir, "week11_nominal_100x100x24_center_side"), 45.0}, {}},
    {{"100x100x24 back+dimple", JoinPath(baseDir, "week11_nominal_100x100x24_center_back_dimple_r3mm"), 45.0}, {}},
  };

  try {
    for (auto& run : all) {
      run.points = ReadEfficiencyMap(JoinPath(run.spec.dir, "efficiency_map.csv"));
    }
  }
  catch (const std::exception& exc) {
    std::cerr << exc.what() << std::endl;
    return;
  }

  std::vector<RunData> sizeRuns = {all[0], all[1], all[2]};
  std::vector<RunData> placementRuns = {all[2], all[3], all[4]};
  std::vector<RunData> placementRatios = {all[3], all[4]};

  DrawMetricPanel(sizeRuns,
                  Metric::kCollectionEfficiency,
                  "Week 11 nominal tile-size scan: collection efficiency",
                  outDir,
                  "root_week11_tile_size_collection_efficiency_panel");
  DrawMetricPanel(sizeRuns,
                  Metric::kDetectedPerEvent,
                  "Week 11 nominal tile-size scan: detected yield",
                  outDir,
                  "root_week11_tile_size_detected_yield_panel");
  DrawMetricPanel(placementRuns,
                  Metric::kCollectionEfficiency,
                  "Week 11 100x100x24 placement scan: collection efficiency",
                  outDir,
                  "root_week11_100x100x24_placement_collection_efficiency_panel");
  DrawMetricPanel(placementRuns,
                  Metric::kDetectedPerEvent,
                  "Week 11 100x100x24 placement scan: detected yield",
                  outDir,
                  "root_week11_100x100x24_placement_detected_yield_panel");
  DrawRatioPanel(all[2],
                 placementRatios,
                 Metric::kCollectionEfficiency,
                 "Week 11 100x100x24 placement / center-back: collection efficiency",
                 outDir,
                 "root_week11_100x100x24_placement_collection_ratio_to_back_panel");
  DrawRatioPanel(all[2],
                 placementRatios,
                 Metric::kDetectedPerEvent,
                 "Week 11 100x100x24 placement / center-back: detected yield",
                 outDir,
                 "root_week11_100x100x24_placement_detected_ratio_to_back_panel");

  std::vector<SummaryRow> summaries;
  for (const auto& run : all) {
    summaries.push_back(BuildSummary(run, "full", false));
    summaries.push_back(BuildSummary(run, "fiducial", true));
  }

  const TString summaryCsv = JoinPath(outDir, "root_week11_nominal_prediction_summary.csv");
  WriteSummaryCsv(summaries, summaryCsv);
  DrawSummaryBars(summaries, outDir);
  PrintCoreSummary(summaries);

  std::cout << "Wrote Week 11 nominal prediction plots to " << outDir << std::endl;
  std::cout << "Wrote summary CSV to " << summaryCsv << std::endl;
}

#endif
