# Headless smoke test of the dashboard against the bundled demo database.
#   Rscript r/tests/smoke.R /tmp/pubtrack-smoke
# Set PUBTRACK_DB to test against another database. Exercises every output through
# shiny::testServer and writes each PNG export to the output folder (default tempdir()).

suppressPackageStartupMessages(library(shiny))
args <- commandArgs(trailingOnly = TRUE)
out_dir <- if (length(args)) args[[1]] else tempdir()
dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
app_dir <- normalizePath(file.path(dirname(sub("--file=", "", grep("--file=", commandArgs(), value = TRUE))), ".."))
if (!nzchar(Sys.getenv("PUBTRACK_DB"))) Sys.setenv(PUBTRACK_DB = file.path(app_dir, "tests", "fixtures", "demo.sqlite"))
app <- shinyAppDir(app_dir)

testServer(app, {
  # Defined here: testServer does not expose app.R's top level helpers to this block.
  save_png <- function(file, plot) {
    dev <- if (requireNamespace("ragg", quietly = TRUE)) ragg::agg_png else "png"
    ggplot2::ggsave(file, plot, width = 7, height = 4.5, units = "in", dpi = 300, device = dev, bg = "white")
  }
  session$flushReact()
  d0 <- d()
  cat("runs:", nrow(d0$runs), " works:", nrow(d0$works), " metrics:", nrow(d0$metrics), "\n")
  stopifnot(nrow(papers()) > 0)
  invisible(output$asof); invisible(output$boxes); invisible(output$overview_tbl)
  invisible(output$papers_tbl); invisible(output$runs_tbl); invisible(output$flags_tbl); invisible(output$unmatched_tbl)
  for (m in c("h", "citations", "hc", "fwci_median")) {
    session$setInputs(traj_metric = m)
    save_png(file.path(out_dir, paste0("traj_", m, ".png")), p_traj())
  }
  session$setInputs(papers_tbl_rows_selected = 1)
  plots <- list(hist = p_hist, paper_gs = p_paper_gs, paper_oa = p_paper_oa, hcore = p_hcore, fwci = p_fwci)
  for (nm in names(plots)) save_png(file.path(out_dir, paste0(nm, ".png")), plots[[nm]]())
  cat("overview rows:", nrow(overview()), "\n")
  cat("all outputs rendered; PNGs in", out_dir, "\n")
})
