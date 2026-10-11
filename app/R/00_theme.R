# Look: Weiskittel Lab design system tokens (CRSF house register), shared by the app and the reports.

pt_col <- list(
  ink = "#14201a", ink_muted = "#4a5750", ink_faint = "#5f6b65",
  surface = "#ffffff", sunken = "#f4f6f4", rule = "#d3dcd6",
  crsf_green = "#134a31", crsf_sky = "#79bde9", crsf_pale = "#c8e2f6",
  context = "#b3bbb6", band = "#79bde9",
  ok = "#1d6fa5", warn = "#8a5a00", alert = "#b3261e"
)
pt_cat <- c("#0072b2", "#e69f00", "#009e73", "#cc79a7", "#56b4e9", "#d55e00")
src_cols <- c(scholar = "#0072b2", openalex = "#d55e00")
src_labs <- c(scholar = "Google Scholar", openalex = "OpenAlex")

# Focal author in CRSF green, everyone else from the categorical set in order.
author_palette <- function(ids, focal = NULL, names = NULL) {
  ids <- unique(ids)
  others <- setdiff(ids, focal)
  pal <- setNames(rep(pt_cat, length.out = length(others)), others)
  if (!is.null(focal) && focal %in% ids) pal <- c(setNames(pt_col$crsf_green, focal), pal)
  if (!is.null(names)) names(pal) <- names[match(names(pal), ids)]
  pal
}

theme_pubtrack <- function(base_size = 13) {
  ggplot2::theme_minimal(base_size = base_size) +
    ggplot2::theme(
      text = ggplot2::element_text(colour = pt_col$ink),
      panel.grid.minor = ggplot2::element_blank(),
      panel.grid.major.x = ggplot2::element_blank(),
      panel.grid.major.y = ggplot2::element_line(colour = "#e6ebe8", linewidth = 0.35),
      axis.line.x = ggplot2::element_line(colour = pt_col$ink_muted, linewidth = 0.4),
      axis.ticks.x = ggplot2::element_line(colour = pt_col$ink_muted, linewidth = 0.4),
      axis.text = ggplot2::element_text(colour = pt_col$ink_muted),
      axis.title = ggplot2::element_text(colour = pt_col$ink, face = "bold", size = ggplot2::rel(0.9)),
      legend.position = "top", legend.justification = "left", legend.title = ggplot2::element_blank(),
      legend.text = ggplot2::element_text(colour = pt_col$ink_muted),
      plot.title.position = "plot",
      plot.title = ggplot2::element_text(face = "bold", size = ggplot2::rel(1.1)),
      plot.subtitle = ggplot2::element_text(colour = pt_col$ink_muted),
      plot.caption = ggplot2::element_text(colour = pt_col$ink_faint, hjust = 0, size = ggplot2::rel(0.75)),
      strip.text = ggplot2::element_text(face = "bold", hjust = 0, colour = pt_col$ink),
      plot.background = ggplot2::element_rect(fill = "white", colour = NA),
      panel.background = ggplot2::element_rect(fill = "white", colour = NA)
    )
}

# Scholar venue strings carry volume, issue and pages ("Forest Ecology and Management 260 (7), 1104-1119").
clean_venue <- function(v) {
  v <- sub(",.*$", "", v)
  v <- sub("\\s+\\d+\\s*(\\([^)]*\\))?\\s*$", "", v)
  trimws(v)
}

fmt_n <- function(x, digits = 0) {
  ifelse(is.na(x), "n/a", formatC(round(x, digits), format = "f", digits = digits, big.mark = ","))
}
fmt_pct <- function(x, digits = 0) ifelse(is.na(x), "n/a", paste0(formatC(100 * x, format = "f", digits = digits), "%"))
fmt_signed <- function(x, digits = 0) ifelse(is.na(x), "", ifelse(x > 0, paste0("+", fmt_n(x, digits)), fmt_n(x, digits)))

# Interactive wrapper: ggplotly with house fonts, tidy hover, and a lean mode bar.
pt_plotly <- function(p, tooltip = "text", height = NULL, legend_bottom = FALSE) {
  if (!requireNamespace("plotly", quietly = TRUE)) return(p)
  if (exists("wrap_caption", mode = "function")) p <- wrap_caption(p, 125)
  cap <- p$labels$caption
  has_xtitle <- !is.null(p$labels$x) && !identical(p$labels$x, "") && !is.null(p$labels$x[[1]])
  w <- plotly::ggplotly(p, tooltip = tooltip, height = height)
  n_lines <- if (is.null(cap)) 0 else length(strsplit(cap, "\n", fixed = TRUE)[[1]])
  if (n_lines > 0) {
    w <- plotly::layout(w, annotations = list(list(
      text = gsub("\n", "<br>", cap, fixed = TRUE), xref = "paper", yref = "paper", x = 0, y = 0,
      yshift = if (has_xtitle) -48 else -28, xanchor = "left", yanchor = "top", showarrow = FALSE, align = "left",
      font = list(size = 11, color = pt_col$ink_faint))))
  }
  w <- plotly::layout(w,
    font = list(family = "Source Sans 3, Source Sans Pro, sans-serif", color = pt_col$ink, size = 13),
    hoverlabel = list(bgcolor = "white", bordercolor = pt_col$rule,
                      font = list(family = "Source Sans 3, sans-serif", color = pt_col$ink, size = 13)),
    legend = if (legend_bottom) list(orientation = "h", x = 0, y = -0.18) else list(orientation = "h", x = 0, y = 1.08),
    margin = list(l = 10, r = 10, t = 30, b = 20 + 16 * n_lines + if (has_xtitle && n_lines > 0) 20 else 0), paper_bgcolor = "white", plot_bgcolor = "white"
  )
  plotly::config(w, displaylogo = FALSE,
                 modeBarButtonsToRemove = c("lasso2d", "select2d", "autoScale2d", "toggleSpikelines",
                                            "hoverClosestCartesian", "hoverCompareCartesian"),
                 toImageButtonOptions = list(format = "png", scale = 3))
}

save_png <- function(file, plot, width = 7, height = 4.5) {
  dev <- if (requireNamespace("ragg", quietly = TRUE)) ragg::agg_png else "png"
  if (exists("wrap_caption")) plot <- wrap_caption(plot)
  ggplot2::ggsave(file, plot, width = width, height = height, units = "in", dpi = 300, device = dev, bg = "white")
}
