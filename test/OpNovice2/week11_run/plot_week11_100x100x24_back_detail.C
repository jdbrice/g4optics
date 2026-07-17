#ifndef OPNOVICE2_PLOT_WEEK11_100X100X24_BACK_DETAIL_C
#define OPNOVICE2_PLOT_WEEK11_100X100X24_BACK_DETAIL_C

#include "../plot_efficiency_map.C"

#include <TLine.h>
#include <TMarker.h>
#include <TROOT.h>
#include <TSystem.h>

namespace {

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

  void Pad(double fraction = 0.08)
  {
    if (!Valid()) return;
    const double span = max - min;
    const double pad = span > 0.0 ? span * fraction : std::max(std::abs(max) * fraction, 1.e-6);
    min -= pad;
    max += pad;
  }
};

ValueRange RangeFor(const std::vector<EfficiencyPoint>& points,
                    const std::function<double(const EfficiencyPoint&)>& getter)
{
  ValueRange range;
  for (const auto& point : points) range.Add(getter(point));
  range.Pad();
  return range;
}

double FiducialMeanEfficiency(const std::vector<EfficiencyPoint>& points, double fiducialLimitMm)
{
  const auto stats = SummarizeRegion(points, "fiducial", fiducialLimitMm, true);
  return stats.collectionEfficiency.Mean();
}

void DrawCenterMarker()
{
  auto* xLine = new TLine(-2.0, 0.0, 2.0, 0.0);
  xLine->SetLineColor(kGray + 3);
  xLine->SetLineWidth(3);
  xLine->Draw("same");

  auto* yLine = new TLine(0.0, -2.0, 0.0, 2.0);
  yLine->SetLineColor(kGray + 3);
  yLine->SetLineWidth(3);
  yLine->Draw("same");

  auto* marker = new TMarker(0.0, 0.0, 29);
  marker->SetMarkerColor(kGray + 3);
  marker->SetMarkerSize(1.3);
  marker->Draw("same");
}

void DrawMapPanel(TH2D* hist, const ValueRange& range, double fiducialLimitMm)
{
  gPad->SetLeftMargin(0.11);
  gPad->SetRightMargin(0.17);
  gPad->SetBottomMargin(0.12);
  gPad->SetTopMargin(0.10);
  if (range.Valid()) {
    hist->SetMinimum(range.min);
    hist->SetMaximum(range.max);
  }
  hist->SetStats(0);
  hist->SetContour(100);
  hist->GetZaxis()->SetTitleOffset(1.25);
  hist->Draw("COLZ");
  DrawFiducialBox(fiducialLimitMm);
  DrawCenterMarker();
}

}  // namespace

void plot_week11_100x100x24_back_detail(
  const char* csvPathArg = "week11_run/week11_nominal_100x100x24_center_back/efficiency_map.csv",
  const char* outDirArg = "week11_run/root_plots",
  double fiducialLimitMm = 45.0)
{
  gROOT->SetBatch(true);
  gStyle->SetOptStat(0);
  gStyle->SetPalette(kBird);

  TString outDir(outDirArg);
  if (outDir.EndsWith("/")) outDir.Chop();
  gSystem->mkdir(outDir.Data(), true);

  std::vector<EfficiencyPoint> points;
  try {
    points = ReadEfficiencyMap(csvPathArg);
  }
  catch (const std::exception& exc) {
    std::cerr << exc.what() << std::endl;
    return;
  }

  const auto xs = UniqueSortedValues(points, true);
  const auto ys = UniqueSortedValues(points, false);
  const auto xEdges = MakeBinEdges(xs);
  const auto yEdges = MakeBinEdges(ys);
  const double fidMean = FiducialMeanEfficiency(points, fiducialLimitMm);

  auto* absolute = MakeMap(
    "h_week11_back_absolute_detail",
    "100x100x24 back only, autoscaled;scan command x [mm];scan command y [mm];collection efficiency",
    points,
    xEdges,
    yEdges,
    [](const EfficiencyPoint& point) { return point.collectionEfficiency; });

  auto* relative = MakeMap(
    "h_week11_back_relative_detail",
    "100x100x24 back / fiducial mean;scan command x [mm];scan command y [mm];collection efficiency / fiducial mean",
    points,
    xEdges,
    yEdges,
    [fidMean](const EfficiencyPoint& point) {
      return fidMean != 0.0 ? point.collectionEfficiency / fidMean : kNaN;
    });

  const auto absoluteRange = RangeFor(points, [](const EfficiencyPoint& point) {
    return point.collectionEfficiency;
  });
  const auto relativeRange = RangeFor(points, [fidMean](const EfficiencyPoint& point) {
    return fidMean != 0.0 ? point.collectionEfficiency / fidMean : kNaN;
  });

  auto* canvas = new TCanvas("c_week11_100x100x24_back_detail",
                             "Week 11 100x100x24 center-back detail",
                             1450,
                             650);
  canvas->Divide(2, 1, 0.01, 0.01);
  canvas->cd(1);
  DrawMapPanel(absolute, absoluteRange, fiducialLimitMm);
  canvas->cd(2);
  DrawMapPanel(relative, relativeRange, fiducialLimitMm);

  SaveCanvas(canvas, outDir, "root_week11_100x100x24_back_detail_panel");

  std::cout << std::setprecision(8)
            << "100x100x24 back fiducial mean_eff = " << fidMean
            << std::endl;
  std::cout << "Wrote detail plot to " << outDir << std::endl;
}

#endif
