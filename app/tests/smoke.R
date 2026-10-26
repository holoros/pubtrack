# Headless smoke test: build a bundle from a store, exercise every server output, render every
# ggplot to PNG, and print both impact statements.
#   Rscript app/tests/smoke.R [store_dir] [out_dir]
suppressPackageStartupMessages({ library(shiny); library(dplyr); library(ggplot2) })
args <- commandArgs(trailingOnly = TRUE)
app_dir <- normalizePath(file.path(dirname(sub("--file=", "", grep("--file=", commandArgs(), value = TRUE))), ".."))
store <- if (length(args) >= 1) normalizePath(args[[1]]) else file.path(app_dir, "tests", "fixtures", "demo_store")
out_dir <- if (length(args) >= 2) args[[2]] else file.path(tempdir(), "pubtrack-smoke")
dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
out_dir <- normalizePath(out_dir)
for (f in sort(list.files(file.path(app_dir, "R"), pattern = "\\.R$", full.names = TRUE))) source(f)

b <- build_bundle(store, file.path(store, "authors.yml"), nsim = 500)
bundle <- file.path(out_dir, "bundle.rds")
saveRDS(b, bundle)
Sys.setenv(PUBTRACK_BUNDLE = bundle)
stopifnot(nrow(b$compare) >= 1, nrow(b$papers) > 0, all(!is.na(b$compare$h)))
af <- b$author_fc
stopifnot(all(af$q10 <= af$q50 + 1e-9), all(af$q50 <= af$q90 + 1e-9),
          all(af$q50[af$quantity == "h" & af$k > 0] >= af$q50[af$quantity == "h" & af$k == 0][match(af$author_id[af$quantity == "h" & af$k > 0], af$author_id[af$quantity == "h" & af$k == 0])]))
print(as.data.frame(b$compare[, c("author_id", "n_papers", "citations", "h", "proj_h_12m", "proj_h_12m_lo", "proj_h_12m_hi", "fwci_median")]))

ids <- b$authors$author_id; a <- ids[1]
wid <- b$papers$work_id[b$papers$author_id == a][which.max(b$papers$unit_citations[b$papers$author_id == a])]
plots <- list(
  hist = plot_hist(b, a), traj_h = plot_trajectory(b, ids, "h", "scholar", a), traj_c = plot_trajectory(b, ids, "citations", "scholar", a),
  traj_oa = plot_trajectory(b, ids, "h", "openalex", a), career_c = plot_career(b, ids, "citations", a),
  career_h = plot_career(b, ids, "h", a), fan_h = plot_fan(b, a, "h"), fan_c = plot_fan(b, a, "citations_total"),
  portfolio = plot_portfolio(b, a), hcore = plot_hcore(b, a), lorenz = plot_lorenz(b, ids, a), heat = plot_heat(b, ids, a),
  proj = plot_projected_h(b, ids, a), output = plot_output(b, ids, a), roles = plot_roles(b, ids, a),
  paper_w = plot_paper_weekly(b, a, wid), paper_a = plot_paper_annual(b, a, wid))
for (nm in names(plots)) {
  save_png(file.path(out_dir, paste0(nm, ".png")), plots[[nm]])
  invisible(pt_plotly(plots[[nm]]))
}
kt <- key_indices_table(b$compare[b$compare$author_id == a, ])
stopifnot(nrow(kt) == 21, !any(grepl("^NA|NA to|\\(NA", kt$Value)))
stopifnot(all(is.na(b$compare$proj_h_12m) | b$compare$forecast_ok))   # projections carried only past the gate
stopifnot(all(c("unit_date", "scholar_date", "openalex_date", "h_openalex", "h5", "h5_reported", "author_count_coverage") %in% names(b$compare)))
cat("\nAUTHOR STATEMENT\n", impact_statement(b, a), "\n\nPAPER STATEMENT\n", paper_statement(b, a, wid), "\n\n")
for (rk in c("fwci", "rate")) cat(rk, ":", impact_statement(b, a, rk, 2, FALSE), "\n\n")

app <- shinyAppDir(app_dir)
testServer(app, {
  session$setInputs(focal = a, compare = ids, source = "scholar", traj_metric = "h", traj_recon = TRUE,
                    rep_author = a, rep_paper = as.character(wid), rep_rank = "citations", rep_ntop = 3, rep_fc = TRUE)
  for (o in c("asof", "vboxes", "p_portfolio", "p_hist", "p_fan_h", "p_fan_c", "p_heat", "p_career_c", "p_career_h",
              "p_proj", "p_lorenz", "p_output", "p_roles", "compare_tbl", "p_traj", "delta_tbl", "papers_tbl",
              "p_fan_h2", "p_fan_c2", "frontier_tbl", "risers_tbl", "p_hcore", "stmt_author_txt", "report_body",
              "rep_tbl", "rep_top", "runs_tbl", "flags_tbl", "shared_tbl")) {
    cat(o, ""); invisible(output[[o]])
  }
  session$setInputs(papers_tbl_rows_selected = 1)
  cat("paper_panel "); invisible(output$paper_panel); cat("p_pw "); invisible(output$p_pw); cat("p_pa\n"); invisible(output$p_pa)
  for (m in c("citations", "fwci_median", "hc")) { session$setInputs(traj_metric = m); invisible(output$p_traj) }
  session$setInputs(source = "openalex"); invisible(output$p_traj)
  cat("all server outputs rendered\n")
})
cat("PNGs in", out_dir, "\n")
