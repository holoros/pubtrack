# ggplot builders shared by the app (wrapped in plotly) and the static reports.
# Each sets a `text` aesthetic for hover; captions state what is observed and what is modelled.

.wrap <- function(x, n = 60) vapply(x, function(s) paste(strwrap(s, n), collapse = "<br>"), "")
.anames <- function(b, ids) b$authors$name[match(ids, b$authors$author_id)]
.int_breaks <- function(x) { br <- pretty(x); br[br == round(br)] }
empty_plot <- function(msg) {
  ggplot2::ggplot() + ggplot2::annotate("text", x = 0, y = 0, label = msg, colour = pt_col$ink_muted, size = 4.5) +
    ggplot2::theme_void()
}
.unit_lab <- function(u) if (identical(u, "scholar")) "Google Scholar" else "OpenAlex"

# Citations received per calendar year (Scholar profile histogram, or OpenAlex counts if no Scholar).
plot_hist <- function(b, aid) {
  py <- b$pyc[b$pyc$author_id == aid, ]
  src <- "scholar"
  if (is.null(py) || nrow(py) == 0) {
    py <- b$wyc[b$wyc$author_id == aid, ] |> dplyr::group_by(year) |>
      dplyr::summarise(citations = sum(citations), .groups = "drop")
    src <- "openalex"
  }
  cy <- as.integer(format(b$compare$snapshot_date[b$compare$author_id == aid], "%Y"))
  py <- dplyr::mutate(py, partial = year == cy,
                      text = paste0("<b>", year, "</b><br>", fmt_n(citations), " citations", ifelse(partial, " (year to date)", "")))
  ggplot2::ggplot(py, ggplot2::aes(year, citations, text = text)) +
    ggplot2::geom_col(ggplot2::aes(fill = partial), width = 0.78) +
    ggplot2::scale_fill_manual(values = c(`FALSE` = src_cols[[src]], `TRUE` = pt_col$crsf_pale), guide = "none") +
    ggplot2::scale_y_continuous(labels = scales::label_comma(), expand = ggplot2::expansion(mult = c(0, 0.06))) +
    ggplot2::scale_x_continuous(breaks = .int_breaks) +
    ggplot2::labs(x = NULL, y = "Citations yr\u207b\u00b9",
                  caption = paste0(.unit_lab(src), ". Pale bar: current year to date.")) +
    theme_pubtrack()
}

# Any stored metric over calendar time for several authors: weekly snapshots solid, OpenAlex
# year end reconstructions dashed.
plot_trajectory <- function(b, ids, metric = "h", source = "scholar", focal = NULL, recon = TRUE) {
  mlab <- if (metric %in% names(metric_labels)) metric_labels[[metric]] else metric
  m <- b$metrics |> dplyr::filter(author_id %in% ids, metric == !!metric, source == !!source)
  r <- if (recon && source == "openalex" && !is.null(b$recon)) b$recon |>
    dplyr::filter(author_id %in% ids, metric == !!metric) else NULL
  if (recon && source == "scholar" && metric == "citations" && nrow(b$pyc) > 0) {
    r <- b$pyc |> dplyr::filter(author_id %in% ids) |> dplyr::arrange(author_id, year) |>
      dplyr::group_by(author_id) |>
      dplyr::filter(year < as.integer(format(max(m$snapshot_date, Sys.Date()), "%Y"))) |>
      dplyr::mutate(value = cumsum(citations) + pre_hist[1], snapshot_date = as.Date(sprintf("%d-12-31", year))) |>
      dplyr::ungroup()
  }
  if (nrow(m) == 0 && (is.null(r) || nrow(r) == 0)) {
    return(empty_plot(paste0(mlab, " is not available from ", .unit_lab(source), ".")))
  }
  pal <- author_palette(ids, focal)
  lab <- setNames(.anames(b, names(pal)), names(pal))
  m <- dplyr::mutate(m, who = author_id, kind = ifelse(origin == "sync", "Weekly snapshot", "Imported"),
                     text = paste0("<b>", lab[author_id], "</b><br>", format(snapshot_date, "%d %b %Y"), "<br>",
                                   mlab, ": ", ifelse(abs(value) < 10 & value != round(value), formatC(value, format = "f", digits = 2), fmt_n(value))))
  g <- ggplot2::ggplot(mapping = ggplot2::aes(snapshot_date, value, colour = who, group = who, text = text))
  if (!is.null(r) && nrow(r) > 0) {
    r <- dplyr::mutate(r, who = author_id,
                       text = paste0("<b>", lab[author_id], "</b><br>", format(snapshot_date, "%Y"), " year end (reconstructed)<br>",
                                     fmt_n(value)))
    g <- g + ggplot2::geom_line(data = r, linetype = "22", linewidth = 0.6, alpha = 0.8) +
      ggplot2::geom_point(data = r, shape = 21, fill = "white", size = 1.6)
  }
  g + ggplot2::geom_line(data = m[m$kind == "Weekly snapshot", ], linewidth = 0.9) +
    ggplot2::geom_point(data = m, size = 1.4) +
    ggplot2::scale_colour_manual(values = pal, labels = lab) +
    ggplot2::scale_y_continuous(labels = scales::label_comma()) +
    ggplot2::labs(x = NULL, y = mlab,
                  caption = paste0(.unit_lab(source), ". Solid: weekly snapshots. Dashed: year end values reconstructed from ",
                                   if (source == "openalex") "per work yearly counts (2012 onward)." else
                                     "the profile's citations per year, offset by citations the histogram predates.")) +
    theme_pubtrack()
}

# Careers aligned by academic age (years since first publication).
plot_career <- function(b, ids, what = c("citations", "h"), focal = NULL) {
  what <- match.arg(what)
  pal <- author_palette(ids, focal)
  lab <- setNames(.anames(b, names(pal)), names(pal))
  fy <- setNames(b$authors$first_year, b$authors$author_id)
  if (what == "citations") {
    if (nrow(b$pyc) == 0) return(empty_plot("No Google Scholar citation history for the selected authors."))
    d <- b$pyc |> dplyr::filter(author_id %in% ids) |> dplyr::arrange(author_id, year) |>
      dplyr::group_by(author_id) |> dplyr::mutate(value = cumsum(citations) + pre_hist[1]) |> dplyr::ungroup() |>
      dplyr::mutate(age = year - fy[author_id] + 1)
    ylab <- "Cumulative citations"
    cap <- "Google Scholar citations per year, cumulated (offset by citations the profile histogram predates) and aligned on each author's first publication year. Authors without a Scholar profile are not shown."
  } else {
    if (is.null(b$recon)) return(empty_plot("No OpenAlex year end reconstruction yet."))
    d <- b$recon |> dplyr::filter(author_id %in% ids, metric == "h") |>
      dplyr::mutate(age = year - fy[author_id] + 1)
    now <- b$compare |> dplyr::filter(author_id %in% ids, !is.na(h_openalex)) |>
      dplyr::transmute(author_id, value = h_openalex, age = academic_age, year = as.integer(format(openalex_date, "%Y")))
    d <- dplyr::bind_rows(d, now)
    ylab <- "h index (OpenAlex)"; cap <- "OpenAlex year end h (reconstructed from per work yearly counts, 2012 onward) joined to the current OpenAlex h, so one source throughout. Grey lines: Hirsch m = 1, 2, 3 (h = m times academic age)."
  }
  d <- dplyr::filter(d, !is.na(age), age >= 1) |>
    dplyr::mutate(text = paste0("<b>", lab[author_id], "</b><br>Academic age ", age, " (", year, ")<br>", fmt_n(value)))
  g <- ggplot2::ggplot(d, ggplot2::aes(age, value, colour = author_id, group = author_id, text = text))
  if (what == "h") {
    mx <- max(d$age, 1)
    ref <- tibble::tibble(m = rep(1:3, each = 2), age = rep(c(0, mx), 3)) |> dplyr::mutate(value = m * age)
    g <- g + ggplot2::geom_line(data = ref, ggplot2::aes(age, value, group = m), inherit.aes = FALSE,
                                colour = pt_col$context, linewidth = 0.4, linetype = "dotted") +
      ggplot2::coord_cartesian(ylim = c(0, max(d$value) * 1.08))
  }
  g + ggplot2::geom_line(linewidth = 1) + ggplot2::geom_point(size = 1.3) +
    ggplot2::scale_colour_manual(values = pal, labels = lab) +
    ggplot2::scale_y_continuous(labels = scales::label_comma()) +
    ggplot2::labs(x = "Academic age (years)", y = ylab, caption = cap) +
    theme_pubtrack()
}

# Forecast fan: observed path plus 80% and 95% intervals out three years.
plot_fan <- function(b, aid, quantity = c("h", "citations_total")) {
  quantity <- match.arg(quantity)
  fc <- b$author_fc |> dplyr::filter(author_id == aid, quantity == !!quantity)
  unit <- b$compare$unit[b$compare$author_id == aid]
  obs <- b$metrics |> dplyr::filter(author_id == aid, source == unit, origin == "sync",
                                    metric == ifelse(quantity == "h", "h", "citations"))
  hist <- if (quantity == "citations_total" && unit == "scholar" && nrow(b$pyc[b$pyc$author_id == aid, ]) > 0) {
    b$pyc |> dplyr::filter(author_id == aid) |> dplyr::arrange(year) |>
      dplyr::mutate(value = cumsum(citations) + pre_hist[1], snapshot_date = as.Date(sprintf("%d-12-31", year))) |>
      dplyr::filter(snapshot_date < min(fc$date))
  } else if (quantity == "h" && !is.null(b$recon)) {
    b$recon |> dplyr::filter(author_id == aid, metric == "h")
  } else NULL
  fc <- dplyr::mutate(fc, text = paste0("<b>", format(date, "%b %Y"), "</b><br>Median ", fmt_n(q50),
                                        "<br>80%: ", fmt_n(q10), " to ", fmt_n(q90), "<br>95%: ", fmt_n(q025), " to ", fmt_n(q975)))
  g <- ggplot2::ggplot(fc, ggplot2::aes(date)) +
    ggplot2::geom_ribbon(ggplot2::aes(ymin = q025, ymax = q975), fill = pt_col$band, alpha = 0.25) +
    ggplot2::geom_ribbon(ggplot2::aes(ymin = q10, ymax = q90), fill = pt_col$band, alpha = 0.45) +
    ggplot2::geom_line(ggplot2::aes(y = q50), colour = pt_col$crsf_green, linewidth = 1, linetype = "22") +
    ggplot2::geom_point(ggplot2::aes(y = q50, text = text), colour = pt_col$crsf_green, size = 2)
  if (!is.null(hist) && nrow(hist) > 0) {
    hist <- dplyr::mutate(hist, text = paste0(format(snapshot_date, "%Y"), " year end<br>", fmt_n(value)))
    g <- g + ggplot2::geom_line(data = hist, ggplot2::aes(snapshot_date, value), colour = pt_col$ink_muted, linewidth = 0.6) +
      ggplot2::geom_point(data = hist, ggplot2::aes(snapshot_date, value, text = text), colour = pt_col$ink_muted, size = 1.2)
  }
  if (nrow(obs) > 0) {
    obs <- dplyr::mutate(obs, text = paste0(format(snapshot_date, "%d %b %Y"), "<br>observed ", fmt_n(value)))
    g <- g + ggplot2::geom_line(data = obs, ggplot2::aes(snapshot_date, value), colour = pt_col$crsf_green, linewidth = 1) +
      ggplot2::geom_point(data = obs, ggplot2::aes(snapshot_date, value, text = text), colour = pt_col$crsf_green, size = 1.4)
  }
  g + ggplot2::scale_y_continuous(labels = scales::label_comma()) +
    ggplot2::labs(x = NULL, y = if (quantity == "h") "h index" else "Total citations",
                  caption = paste0(.unit_lab(unit), ". Green: weekly snapshots and median projection. Grey: ",
                                   if (quantity == "h") "OpenAlex year end h, reconstructed (lower coverage than Scholar)" else "profile citations per year, cumulated",
                                   ".\nBands: 80% and 95% prediction intervals from ", b$nsim,
                                   " simulations of per paper citation rates; existing papers only.")) +
    theme_pubtrack()
}

# Portfolio map: every paper by age and citations, sized by projected next year citations.
plot_portfolio <- function(b, aid) {
  p <- b$papers |> dplyr::filter(author_id == aid, !is.na(unit_citations), !is.na(year))
  unit <- b$compare$unit[b$compare$author_id == aid]
  h <- h_of(p$unit_citations)
  cy <- as.integer(format(b$compare$snapshot_date[b$compare$author_id == aid], "%Y"))
  p <- p |> dplyr::mutate(
    tier = dplyr::case_when(top1 %in% 1 ~ "Top 1% (FWCI)", top10 %in% 1 ~ "Top 10% (FWCI)",
                            unit_citations >= h ~ "h core", TRUE ~ "Other"),
    tier = factor(tier, levels = c("Top 1% (FWCI)", "Top 10% (FWCI)", "h core", "Other")),
    text = paste0("<b>", .wrap(title), "</b><br>", year, ifelse(is.na(venue), "", paste0(", ", .wrap(clean_venue(venue), 50))),
                  "<br>Citations: ", fmt_n(unit_citations), ifelse(is.na(fwci), "", paste0("<br>FWCI: ", formatC(fwci, format = "f", digits = 2))),
                  ifelse(is.na(f12_med), "", paste0("<br>Next 12 months: ", fmt_n(f12_med), " (", fmt_n(f12_lo), " to ", fmt_n(f12_hi), ")"))),
    y = pmax(unit_citations, 0.8))
  ggplot2::ggplot(p, ggplot2::aes(year, y, colour = tier, size = pmax(f12_med, 0.5), text = text)) +
    ggplot2::geom_hline(yintercept = h, colour = pt_col$context, linetype = "dotted") +
    ggplot2::geom_jitter(width = 0.25, height = 0, alpha = 0.8) +
    ggplot2::scale_y_log10(labels = scales::label_comma()) +
    ggplot2::scale_size_area(max_size = 9, guide = "none") +
    ggplot2::scale_colour_manual(values = c(`Top 1% (FWCI)` = "#d55e00", `Top 10% (FWCI)` = "#e69f00",
                                            `h core` = pt_col$crsf_green, Other = pt_col$context), drop = FALSE) +
    ggplot2::scale_x_continuous(breaks = .int_breaks) +
    ggplot2::labs(x = "Publication year", y = "Citations (log scale)",
                  caption = paste0(.unit_lab(unit), " citations. Dotted line: h = ", h,
                                   ". Point area: projected citations in the next 12 months",
                                   if (isTRUE(b$compare$forecast_ok[b$compare$author_id == aid])) "." else
                                     " (forecast check not passed: sizes are indicative only).")) +
    theme_pubtrack()
}

plot_hcore <- function(b, aid) {
  p <- b$papers |> dplyr::filter(author_id == aid, !is.na(unit_citations)) |>
    dplyr::arrange(dplyr::desc(unit_citations)) |> dplyr::mutate(rank = dplyr::row_number())
  h <- h_of(p$unit_citations)
  p <- p |> dplyr::mutate(zone = dplyr::case_when(rank <= h ~ "In the h core",
                                                  rank <= h + 6 & unit_citations >= h - 3 ~ "Within reach of h + 1",
                                                  TRUE ~ "Other"),
                          text = paste0("<b>#", rank, "</b> ", .wrap(title), "<br>", fmt_n(unit_citations), " citations",
                                        ifelse(rank <= h + 1 & unit_citations < h + 1, paste0("<br>needs ", h + 1 - unit_citations, " for h + 1"), "")))
  show <- dplyr::filter(p, rank <= max(2.5 * h, 30))
  ggplot2::ggplot(show, ggplot2::aes(rank, pmax(unit_citations, 0.8), colour = zone, text = text)) +
    ggplot2::geom_line(data = tibble::tibble(x = seq_len(max(show$rank)), y = seq_len(max(show$rank))),
                       ggplot2::aes(x, y), inherit.aes = FALSE, colour = pt_col$context, linetype = "dashed", linewidth = 0.4) +
    ggplot2::geom_vline(xintercept = h + 0.5, colour = pt_col$context, linetype = "dotted") +
    ggplot2::geom_point(size = 2.2) +
    ggplot2::annotate("text", x = h + 1, y = max(show$unit_citations), label = paste0("h = ", h), hjust = 0, vjust = 1,
                      fontface = "bold", colour = pt_col$ink) +
    ggplot2::scale_colour_manual(values = c(`In the h core` = pt_col$crsf_green, `Within reach of h + 1` = "#e69f00",
                                            Other = pt_col$context)) +
    ggplot2::scale_y_log10(labels = scales::label_comma()) +
    ggplot2::labs(x = "Rank", y = "Citations (log scale)",
                  caption = "Dashed: citations = rank. Dotted: edge of the h core.") +
    theme_pubtrack()
}

plot_lorenz <- function(b, ids, focal = NULL) {
  pal <- author_palette(ids, focal); lab <- setNames(.anames(b, names(pal)), names(pal))
  d <- b$papers |> dplyr::filter(author_id %in% ids, !is.na(unit_citations)) |>
    dplyr::group_by(author_id) |> dplyr::arrange(dplyr::desc(unit_citations), .by_group = TRUE) |>
    dplyr::mutate(x = dplyr::row_number() / dplyr::n(), y = cumsum(unit_citations) / sum(unit_citations)) |>
    dplyr::ungroup() |>
    dplyr::mutate(text = paste0("<b>", lab[author_id], "</b><br>Top ", fmt_pct(x), " of papers hold ", fmt_pct(y), " of citations"))
  ggplot2::ggplot(d, ggplot2::aes(x, y, colour = author_id, group = author_id, text = text)) +
    ggplot2::geom_abline(slope = 1, intercept = 0, colour = pt_col$context, linetype = "dotted") +
    ggplot2::geom_line(linewidth = 1) +
    ggplot2::scale_colour_manual(values = pal, labels = lab) +
    ggplot2::scale_x_continuous(labels = scales::label_percent()) +
    ggplot2::scale_y_continuous(labels = scales::label_percent(), limits = c(0, 1)) +
    ggplot2::labs(x = "Share of papers (most cited first)", y = "Share of citations",
                  caption = paste0("Concentration of citations in each author's own unit. A curve near the top left means impact rests on a few papers.",
                                   .mixed_note(b, ids))) +
    theme_pubtrack()
}

# Appended to captions of panels that show each author in their own unit.
.mixed_note <- function(b, ids) {
  u <- unique(b$compare$unit[b$compare$author_id %in% ids])
  if (length(u) > 1) " Units are mixed across authors (GS = Google Scholar, OA = OpenAlex); Scholar counts run higher for the same record." else ""
}

# Standardized profile: each metric as a percentile within the tracked set.
compare_metrics <- c(citations = "Citations", h = "h index", g = "g index", i10 = "i10 index", m_quotient = "m quotient",
                     hc = "Contemporary h", hI_norm = "hI norm", hm = "hm index", awcr = "AWCR", cites_per_paper = "Citations per paper",
                     cites_last_full_year = "Citations last year", growth_yoy = "Growth year on year",
                     fwci_median = "Median FWCI (OA)", top10_share = "Top 10% share (OA)",
                     proj_h_12m = "Projected h (12 mo)", share_first_or_sole = "First or sole author share (OA)",
                     # Annualized: rates that compare authors at different career stages. Rows marked
                     # (OA) are OpenAlex for every author; the rest are in each author's own unit.
                     papers_per_year = "Papers per career year", cites_per_year = "Citations per career year",
                     cites_per_paper_year_median = "Citations per paper per year (median)",
                     cites_per_year_5y = "Citations per year, last 5 complete y (OA)", h5 = "h5, last 5 complete y (OA)",
                     papers_5y = "Papers, last 5 complete y (OA)", h_recent = "h of papers from last 5 y (OA)",
                     cites_per_recent_paper = "Citations per recent paper (OA)")
heat_pct_metrics <- c("growth_yoy", "top10_share", "share_first_or_sole")
heat_dec_metrics <- c("m_quotient", "cites_per_paper", "fwci_median", "papers_per_year", "cites_per_paper_year_median",
                      "cites_per_recent_paper", "hm")
fmt_heat <- function(metric, value) {
  ifelse(is.na(value), "", ifelse(metric %in% heat_pct_metrics, fmt_pct(value),
                                   ifelse(metric %in% heat_dec_metrics, fmt_n(value, 2), fmt_n(value))))
}

plot_heat <- function(b, ids, focal = NULL) {
  cm <- b$compare |> dplyr::filter(author_id %in% ids) |>
    dplyr::mutate(col = paste0(name, " (", ifelse(unit == "scholar", "GS", "OA"), ")"),
                  col = ifelse(duplicated(col) | duplicated(col, fromLast = TRUE), paste0(col, " [", author_id, "]"), col))
  mixed <- length(unique(cm$unit)) > 1
  d <- cm |> dplyr::select(author_id, col, dplyr::any_of(names(compare_metrics))) |>
    tidyr::pivot_longer(-c(author_id, col), names_to = "metric", values_to = "value") |>
    dplyr::group_by(metric) |>
    dplyr::mutate(pct = if (sum(!is.na(value)) > 1) (rank(value, na.last = "keep") - 1) / (sum(!is.na(value)) - 1) else NA_real_) |>
    dplyr::ungroup() |>
    dplyr::mutate(metric_lab = factor(compare_metrics[metric], levels = rev(compare_metrics)),
                  col = factor(col, levels = cm$col[order(cm$author_id != (focal %||% ""), -cm$h)]),
                  lab = fmt_heat(metric, value),
                  text = paste0("<b>", col, "</b><br>", metric_lab, ": ", lab, ifelse(is.na(pct), "", paste0("<br>Rank within set: ", fmt_pct(pct)))))
  ggplot2::ggplot(d, ggplot2::aes(col, metric_lab, fill = pct, text = text)) +
    ggplot2::geom_tile(colour = "white", linewidth = 1.2) +
    ggplot2::geom_text(ggplot2::aes(label = lab), size = 3.6, colour = pt_col$ink) +
    ggplot2::scale_fill_gradient(low = "#f4f6f4", high = pt_col$crsf_sky, na.value = "white", limits = c(0, 1), guide = "none") +
    ggplot2::scale_x_discrete(position = "top") +
    ggplot2::labs(x = NULL, y = NULL,
                  caption = paste0("Cell shade: rank within the selected authors (darker is higher). Column unit: GS = Google Scholar, OA = OpenAlex. ",
                                   "Rows marked (OA) are OpenAlex for everyone.",
                                   if (mixed) " Unmarked rows mix units across columns; Scholar counts run higher than OpenAlex for the same record." else "")) +
    theme_pubtrack() + ggplot2::theme(panel.grid = ggplot2::element_blank(), axis.line.x = ggplot2::element_blank(),
                                      axis.ticks.x = ggplot2::element_blank(), axis.text.x = ggplot2::element_text(face = "bold", colour = pt_col$ink))
}

plot_projected_h <- function(b, ids, focal = NULL) {
  pal <- author_palette(ids, focal)
  cm <- b$compare |> dplyr::filter(author_id %in% ids, !is.na(proj_h_12m)) |>
    dplyr::mutate(name = stats::reorder(name, proj_h_12m),
                  text = paste0("<b>", name, "</b><br>Now: ", h, "<br>In 12 months: ", proj_h_12m, " (80% ", proj_h_12m_lo, " to ", proj_h_12m_hi, ")"))
  ggplot2::ggplot(cm, ggplot2::aes(y = name, colour = author_id, text = text)) +
    ggplot2::geom_segment(ggplot2::aes(x = proj_h_12m_lo, xend = proj_h_12m_hi, yend = name), linewidth = 3, alpha = 0.35) +
    ggplot2::geom_point(ggplot2::aes(x = h), shape = 21, fill = "white", size = 3) +
    ggplot2::geom_point(ggplot2::aes(x = proj_h_12m), size = 3.5) +
    ggplot2::scale_colour_manual(values = pal, guide = "none") +
    ggplot2::labs(x = "h index (each author's own unit)", y = NULL,
                  caption = paste0("Open circle: current h. Solid: median projection in 12 months. Bar: 80% interval. Authors whose forecast check failed are not shown.",
                                   .mixed_note(b, ids))) +
    theme_pubtrack() + ggplot2::theme(panel.grid.major.x = ggplot2::element_line(colour = "#e6ebe8"),
                                      panel.grid.major.y = ggplot2::element_blank())
}

plot_output <- function(b, ids, focal = NULL, from = NULL) {
  pal <- author_palette(ids, focal); lab <- setNames(.anames(b, names(pal)), names(pal))
  d <- b$papers |> dplyr::filter(author_id %in% ids, !is.na(year)) |> dplyr::count(author_id, year, name = "n")
  from <- from %||% (max(d$year) - 14)
  d <- d |> dplyr::filter(year >= from) |>
    dplyr::mutate(text = paste0("<b>", lab[author_id], "</b><br>", year, ": ", n, " papers"))
  ggplot2::ggplot(d, ggplot2::aes(year, n, fill = author_id, text = text)) +
    ggplot2::geom_col(position = ggplot2::position_dodge(preserve = "single"), width = 0.8) +
    ggplot2::scale_fill_manual(values = pal, labels = lab) +
    ggplot2::scale_x_continuous(breaks = .int_breaks) +
    ggplot2::scale_y_continuous(expand = ggplot2::expansion(mult = c(0, 0.06))) +
    ggplot2::labs(x = NULL, y = "Papers per year",
                  caption = paste0("Publication year as recorded on each profile, in each author's own unit (Scholar lists what the profile lists; OpenAlex excludes datasets and folds versions).",
                                   .mixed_note(b, ids))) +
    theme_pubtrack()
}

plot_roles <- function(b, ids, focal = NULL) {
  d <- b$papers |> dplyr::filter(author_id %in% ids, role != "Unknown") |>
    dplyr::count(author_id, role) |> dplyr::group_by(author_id) |> dplyr::mutate(share = n / sum(n)) |> dplyr::ungroup() |>
    dplyr::mutate(name = .anames(b, author_id), role = factor(role, levels = c("Sole", "First", "Middle", "Last")),
                  text = paste0("<b>", name, "</b><br>", role, " author: ", n, " papers (", fmt_pct(share), ")"))
  ggplot2::ggplot(d, ggplot2::aes(share, name, fill = role, text = text)) +
    ggplot2::geom_col(width = 0.7) +
    ggplot2::scale_fill_manual(values = c(Sole = pt_col$crsf_green, First = "#0072b2", Middle = pt_col$context, Last = "#e69f00")) +
    ggplot2::scale_x_continuous(labels = scales::label_percent(), expand = c(0, 0)) +
    ggplot2::labs(x = "Share of papers with known author order", y = NULL, caption = "Author order from OpenAlex.") +
    theme_pubtrack() + ggplot2::theme(panel.grid.major.y = ggplot2::element_blank())
}

# One paper: weekly Scholar counts, and OpenAlex annual counts with the fitted trend and forecast.
plot_paper_weekly <- function(b, aid, wid) {
  h <- b$paper_hist |> dplyr::filter(author_id == aid, work_id == wid, origin == "sync") |>
    dplyr::mutate(text = paste0(src_labs[source], "<br>", format(snapshot_date, "%d %b %Y"), ": ", fmt_n(citations)))
  ggplot2::ggplot(h, ggplot2::aes(snapshot_date, citations, colour = source, group = source, text = text)) +
    ggplot2::geom_line(linewidth = 0.9) + ggplot2::geom_point(size = 1.5) +
    ggplot2::scale_colour_manual(values = src_cols, labels = src_labs) +
    ggplot2::labs(x = NULL, y = "Cumulative citations", caption = "Weekly snapshots.") +
    theme_pubtrack()
}

plot_paper_annual <- function(b, aid, wid) {
  p <- b$papers[b$papers$author_id == aid & b$papers$work_id == wid, ]
  cy0 <- as.integer(format(b$compare$snapshot_date[b$compare$author_id == aid], "%Y"))
  y <- b$wyc |> dplyr::filter(author_id == aid, work_id == wid) |>
    dplyr::mutate(partial = year == cy0,
                  text = paste0(year, ": ", citations, " OpenAlex citations", ifelse(partial, " (year to date)", "")))
  fc <- b$paper_fc |> dplyr::filter(author_id == aid, work_id == wid) |>
    dplyr::mutate(text = paste0("Months ", 12 * (k - 1) + 1, " to ", 12 * k, " from now<br>Median ", fmt_n(q50),
                                "<br>80%: ", fmt_n(q10), " to ", fmt_n(q90), "<br>95%: ", fmt_n(q025), " to ", fmt_n(q975)))
  k <- p$k_scale %||% 1
  g <- ggplot2::ggplot() +
    ggplot2::geom_col(data = y, ggplot2::aes(year, citations * k, text = text, fill = partial), width = 0.75) +
    ggplot2::scale_fill_manual(values = c(`FALSE` = src_cols[["openalex"]], `TRUE` = "#f3c9a8"), guide = "none")
  if (nrow(fc)) {
    g <- g + ggplot2::geom_linerange(data = fc, ggplot2::aes(x = year_mid, ymin = q025, ymax = q975, text = text),
                                     colour = pt_col$band, linewidth = 1.2) +
      ggplot2::geom_linerange(data = fc, ggplot2::aes(x = year_mid, ymin = q10, ymax = q90, text = text),
                              colour = "#1d6fa5", linewidth = 3) +
      ggplot2::geom_point(data = fc, ggplot2::aes(year_mid, q50, text = text), colour = pt_col$crsf_green, size = 2.6)
  }
  if (identical(p$fc_method, "trend")) {
    cy <- as.integer(format(b$compare$snapshot_date[b$compare$author_id == aid], "%Y"))
    tt <- seq(cy - 6, cy + 2.5, by = 0.25)
    h <- pmax(tt - (cy - 1), 0)
    lin <- p$fit_a + p$fit_b * (pmin(tt, cy - 1) - (cy - 1) + p$fit_t0) + p$fit_b * (1 - FC_DAMP^h) / (1 - FC_DAMP)
    fit <- tibble::tibble(x = tt, yhat = k * exp(lin))
    g <- g + ggplot2::geom_line(data = fit, ggplot2::aes(x, yhat), colour = pt_col$crsf_green, linetype = "22", linewidth = 0.7)
  }
  g + ggplot2::scale_x_continuous(breaks = .int_breaks) +
    ggplot2::scale_y_continuous(expand = ggplot2::expansion(mult = c(0, 0.06))) +
    ggplot2::labs(x = NULL, y = "Citations yr\u207b\u00b9",
                  caption = paste0("Bars: OpenAlex citations per year", if (k != 1) paste0(" scaled by ", formatC(k, format = "f", digits = 2), " to Scholar units") else "",
                                   ". Pale bar: year to date.\nPoints: successive 12 month windows from now with 80% (dark) and 95% (light) intervals; method: ", p$fc_method,
                                   if (isTRUE(b$compare$forecast_ok[b$compare$author_id == aid])) "." else " (forecast check not passed: indicative only).")) +
    theme_pubtrack()
}


# Wrap long captions for static output; plotly shows the same caption as an annotation (pt_plotly).
wrap_caption <- function(p, width = 92) {
  cap <- p$labels$caption
  if (is.null(cap) || !nzchar(cap)) return(p)
  paras <- strsplit(cap, "\n", fixed = TRUE)[[1]]
  p$labels$caption <- paste(unlist(lapply(paras, function(x) strwrap(x, width))), collapse = "\n")
  p
}
