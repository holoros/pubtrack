# pubtrack dashboard. Reads the pubtrack SQLite database read only.
# Launch with `pubtrack dashboard`, or: PUBTRACK_DB=/path/to/pubtrack.sqlite Rscript -e "shiny::runApp('r')"
#
# Packages: shiny, bslib, DBI, RSQLite, dplyr, tidyr, ggplot2, DT, scales (ragg optional for PNG export)

suppressPackageStartupMessages({
  library(shiny)
  library(bslib)
  library(DBI)
  library(RSQLite)
  library(dplyr)
  library(tidyr)
  library(ggplot2)
  library(DT)
  library(scales)
})

db_path <- path.expand(Sys.getenv("PUBTRACK_DB", "~/.local/share/pubtrack/pubtrack.sqlite"))
if (!file.exists(db_path)) {
  stop("Database not found at ", db_path, ". Run `pubtrack init` and `pubtrack sync` first.")
}

# ---------------------------------------------------------------- look

src_cols <- c(scholar = "#0072B2", openalex = "#D55E00") # Okabe-Ito pair, colourblind safe
src_labs <- c(scholar = "Google Scholar", openalex = "OpenAlex")

theme_pubtrack <- function(base_size = 12) {
  theme_classic(base_size = base_size) +
    theme(
      panel.grid.major.y = element_line(colour = "grey90", linewidth = 0.3),
      axis.line = element_line(colour = "grey30", linewidth = 0.4),
      axis.ticks = element_line(colour = "grey30", linewidth = 0.4),
      axis.text = element_text(colour = "grey20"),
      legend.position = "top",
      legend.justification = "left",
      legend.title = element_blank(),
      plot.title.position = "plot",
      plot.title = element_text(face = "bold", size = rel(1.05)),
      plot.caption = element_text(colour = "grey40", hjust = 0, size = rel(0.8)),
      strip.background = element_blank(),
      strip.text = element_text(face = "bold", hjust = 0)
    )
}
theme_set(theme_pubtrack())

save_png <- function(file, plot) {
  dev <- if (requireNamespace("ragg", quietly = TRUE)) ragg::agg_png else "png"
  ggsave(file, plot, width = 7, height = 4.5, units = "in", dpi = 300, device = dev, bg = "white")
}

# ---------------------------------------------------------------- data

q <- function(sql, params = NULL) {
  con <- dbConnect(SQLite(), db_path, flags = SQLITE_RO)
  on.exit(dbDisconnect(con))
  if (is.null(params)) dbGetQuery(con, sql) else dbGetQuery(con, sql, params = params)
}

h_of <- function(x) { x <- sort(x, decreasing = TRUE); sum(x >= seq_along(x)) }
g_of <- function(x) { x <- sort(x, decreasing = TRUE); sum(cumsum(x) >= seq_along(x)^2) }

load_all <- function() {
  runs <- q("SELECT * FROM runs ORDER BY run_id")
  runs$snapshot_date <- as.Date(runs$snapshot_date)
  last_ok <- function(src) {
    r <- runs[runs[[paste0(src, "_status")]] %in% "ok" & runs$origin == "sync", ]
    if (nrow(r) == 0) NA_integer_ else r$run_id[which.max(as.numeric(r$snapshot_date) * 1e6 + r$run_id)]
  }
  rs <- last_ok("scholar")
  ro <- last_ok("openalex")
  works <- q("SELECT * FROM works")
  ws <- q("SELECT s.run_id, s.work_id, s.source, s.citations, r.snapshot_date, r.origin
           FROM work_snapshots s JOIN runs r USING (run_id)")
  ws$snapshot_date <- as.Date(ws$snapshot_date)
  metrics <- q("SELECT m.run_id, m.source, m.metric, m.value, r.snapshot_date, r.origin
                FROM metrics m JOIN runs r USING (run_id)")
  metrics$snapshot_date <- as.Date(metrics$snapshot_date)
  list(
    runs = runs, rs = rs, ro = ro, works = works, ws = ws, metrics = metrics,
    wyc = q("SELECT * FROM work_year_counts WHERE source = 'openalex'"),
    impact = if (is.na(ro)) NULL else q("SELECT * FROM work_impact WHERE run_id = ?", list(ro)),
    pyc = if (is.na(rs)) NULL else q("SELECT year, citations FROM profile_year_counts WHERE run_id = ?", list(rs)),
    profile = if (is.na(rs)) NULL else q("SELECT * FROM profile_snapshots WHERE run_id = ?", list(rs)),
    flags = q("SELECT f.run_id, f.kind, f.detail, w.gs_id, w.openalex_id FROM flags f
               LEFT JOIN works w USING (work_id)
               WHERE f.run_id = (SELECT MAX(run_id) FROM runs WHERE origin = 'sync')")
  )
}

included <- function(works) works$work_id[works$excluded == 0 & is.na(works$merged_into)]

# Per paper table at the latest successful run of each source, merged duplicates folded in.
paper_table <- function(d) {
  w <- d$works
  cur <- function(src, run) {
    if (is.na(run)) return(tibble(work_id = integer(), citations = integer()))
    s <- d$ws %>% filter(source == src, run_id == run) %>% select(work_id, citations)
    merged <- w %>% filter(!is.na(merged_into), excluded == 0) %>% select(work_id, merged_into)
    s %>%
      left_join(merged, by = "work_id") %>%
      mutate(work_id = coalesce(merged_into, work_id)) %>%
      group_by(work_id) %>% summarise(citations = sum(citations), .groups = "drop")
  }
  vel <- d$ws %>%
    filter(source == "scholar", origin == "sync", snapshot_date > Sys.Date() - 365) %>%
    group_by(work_id) %>%
    filter(n() >= 8) %>%
    summarise(velocity = unname(coef(lm(citations ~ as.numeric(snapshot_date)))[2]) * 365.25, .groups = "drop")
  imp <- if (is.null(d$impact)) tibble(work_id = integer(), fwci = numeric(), pct_value = numeric(),
                                         top10 = integer(), top1 = integer()) else d$impact
  w %>%
    filter(work_id %in% included(w)) %>%
    left_join(cur("scholar", d$rs) %>% rename(scholar = citations), by = "work_id") %>%
    left_join(cur("openalex", d$ro) %>% rename(openalex = citations), by = "work_id") %>%
    left_join(vel, by = "work_id") %>%
    left_join(imp %>% select(work_id, fwci, pct_value, top10, top1), by = "work_id") %>%
    arrange(desc(scholar), desc(openalex))
}

# OpenAlex reconstruction of year end indices: c_i(Y) = cited_by_count now - citations after Y.
# Exact within the ten year window OpenAlex keeps per work; drawn dashed, never as observations.
reconstruct <- function(d) {
  if (is.na(d$ro)) return(NULL)
  now <- d$ws %>% filter(source == "openalex", run_id == d$ro, work_id %in% included(d$works)) %>%
    select(work_id, citations) %>%
    left_join(d$works %>% select(work_id, year), by = "work_id")
  cy <- as.integer(format(d$runs$snapshot_date[d$runs$run_id == d$ro], "%Y"))
  bind_rows(lapply(seq(cy - 9, cy - 1), function(Y) {
    after <- d$wyc %>% filter(year > Y) %>% group_by(work_id) %>% summarise(a = sum(citations), .groups = "drop")
    cc <- now %>% filter(!is.na(year), year <= Y) %>% left_join(after, by = "work_id") %>%
      mutate(c = pmax(citations - coalesce(a, 0L), 0)) %>% pull(c)
    tibble(snapshot_date = as.Date(sprintf("%d-12-31", Y)), source = "openalex",
           metric = c("citations", "h", "g", "i10"),
           value = c(sum(cc), h_of(cc), g_of(cc), sum(cc >= 10)))
  }))
}

metric_labels <- c(
  citations = "Total citations", h = "h index", g = "g index", i10 = "i10 index",
  m_quotient = "m quotient", e = "e index", hc = "Contemporary h (hc)", hI_norm = "hI norm",
  hI_annual = "hI annual", annual_h = "Annual h", awcr = "AWCR", aw = "AW index", awcr_pa = "AWCRpA",
  fwci_median = "Median FWCI", top10_share = "Share in top 10%", top1_share = "Share in top 1%",
  velocity_per_year = "Citation velocity (per year)", interim_rate_per_year = "Interim rate (per year)",
  n_papers = "Papers"
)

# ---------------------------------------------------------------- UI

plot_card <- function(id, title, height = "430px") {
  card(
    full_screen = TRUE,
    card_header(class = "d-flex justify-content-between align-items-center",
                title, downloadButton(paste0("dl_", id), "PNG", class = "btn-sm btn-outline-secondary")),
    plotOutput(id, height = height)
  )
}

ui <- page_navbar(
  title = "pubtrack",
  theme = bs_theme(version = 5, base_font = font_collection("Calibri", "Carlito", "sans-serif"),
                   primary = "#0072B2"),
  nav_panel("Overview",
    uiOutput("boxes"),
    card(card_header("Indices now and change since 4 and 52 weeks ago"), DTOutput("overview_tbl"))
  ),
  nav_panel("Trajectory",
    layout_columns(col_widths = c(6, 6),
      plot_card("p_hist", "Citations received per year (Google Scholar)"),
      card(
        full_screen = TRUE,
        card_header(class = "d-flex justify-content-between align-items-center",
                    "Index over time",
                    div(class = "d-flex gap-2",
                        selectInput("traj_metric", NULL, width = "200px",
                                    choices = setNames(names(metric_labels)[1:13], metric_labels[1:13]),
                                    selected = "h"),
                        downloadButton("dl_p_traj", "PNG", class = "btn-sm btn-outline-secondary"))),
        plotOutput("p_traj", height = "430px")
      )
    )
  ),
  nav_panel("Papers",
    card(card_header("Papers (click a row for its history)"), DTOutput("papers_tbl")),
    layout_columns(col_widths = c(6, 6),
      plot_card("p_paper_gs", "Selected paper: weekly Google Scholar count", "320px"),
      plot_card("p_paper_oa", "Selected paper: OpenAlex citations per year", "320px")
    )
  ),
  nav_panel("h core", plot_card("p_hcore", "Rank against citations (Google Scholar)", "520px")),
  nav_panel("Field impact", plot_card("p_fwci", "Field weighted citation impact by paper (OpenAlex)", "520px")),
  nav_panel("Data quality",
    layout_columns(col_widths = c(12, 6, 6),
      card(card_header("Recent runs"), DTOutput("runs_tbl")),
      card(card_header("Flags on the latest run"), DTOutput("flags_tbl")),
      card(card_header("Unmatched Scholar works"), DTOutput("unmatched_tbl"))
    )
  ),
  nav_spacer(),
  nav_item(textOutput("asof", inline = TRUE))
)

# ---------------------------------------------------------------- server

server <- function(input, output, session) {
  d <- reactivePoll(60000, session,
                    checkFunc = function() q("SELECT MAX(run_id) AS m, MAX(finished) AS f FROM runs"),
                    valueFunc = load_all)
  papers <- reactive(paper_table(d()))

  output$asof <- renderText({
    r <- d()$runs %>% filter(origin == "sync")
    if (nrow(r) == 0) "No runs yet" else paste("Last run", format(max(r$snapshot_date), "%d %b %Y"))
  })

  # ---- overview
  val_at <- function(m, when) {
    m %>% filter(origin == "sync", snapshot_date <= when) %>%
      group_by(source, metric) %>% slice_max(snapshot_date, n = 1, with_ties = FALSE) %>%
      ungroup() %>% select(source, metric, value)
  }
  overview <- reactive({
    m <- d()$metrics
    if (nrow(m) == 0) return(NULL)
    now <- val_at(m, Sys.Date())
    w4 <- val_at(m, Sys.Date() - 28) %>% rename(v4 = value)
    w52 <- val_at(m, Sys.Date() - 364) %>% rename(v52 = value)
    now %>% left_join(w4, by = c("source", "metric")) %>% left_join(w52, by = c("source", "metric")) %>%
      filter(metric %in% names(metric_labels)) %>%
      mutate(d4 = value - v4, d52 = value - v52)
  })

  output$boxes <- renderUI({
    o <- overview()
    if (is.null(o)) return(div(class = "p-3", "No metrics yet. Run `pubtrack sync`."))
    gv <- function(src, met, digits = 0) {
      v <- o$value[o$source == src & o$metric == met]
      if (length(v) == 0) "n/a" else formatC(v, format = "f", digits = digits, big.mark = ",")
    }
    layout_column_wrap(width = 1 / 5,
      value_box("Citations (Scholar)", gv("scholar", "citations")),
      value_box("h index (Scholar)", gv("scholar", "h")),
      value_box("hI norm (Scholar)", gv("scholar", "hI_norm")),
      value_box("Median FWCI (OpenAlex)", gv("openalex", "fwci_median", 2)),
      value_box("Velocity, citations per year", gv("scholar", "velocity_per_year"))
    )
  })

  output$overview_tbl <- renderDT({
    o <- overview()
    req(o)
    fmt <- function(x) ifelse(is.na(x), "", formatC(x, format = "fg", digits = 4, big.mark = ","))
    sgn <- function(x) ifelse(is.na(x), "", ifelse(x > 0, paste0("+", fmt(x)), fmt(x)))
    tbl <- o %>%
      mutate(Metric = metric_labels[metric], now = fmt(value), d4 = sgn(d4), d52 = sgn(d52),
             order = match(metric, names(metric_labels))) %>%
      select(order, Metric, source, now, d4, d52) %>%
      pivot_wider(names_from = source, values_from = c(now, d4, d52)) %>%
      arrange(order) %>% select(-order)
    want <- c("Metric", "now_scholar", "d4_scholar", "d52_scholar", "now_openalex", "d4_openalex", "d52_openalex")
    for (k in setdiff(want, names(tbl))) tbl[[k]] <- ""
    tbl <- tbl[, want]
    names(tbl) <- c("Metric", "Scholar", "4 wk", "52 wk", "OpenAlex", "4 wk ", "52 wk ")
    datatable(tbl, rownames = FALSE, options = list(dom = "t", pageLength = 50, ordering = FALSE,
                                                    columnDefs = list(list(className = "dt-center", targets = 1:6))))
  })

  # ---- trajectory
  p_hist <- reactive({
    pyc <- d()$pyc
    req(pyc, nrow(pyc) > 0)
    cy <- as.integer(format(Sys.Date(), "%Y"))
    ggplot(pyc, aes(year, citations, alpha = year == cy)) +
      geom_col(fill = src_cols[["scholar"]], width = 0.75) +
      scale_alpha_manual(values = c(`FALSE` = 1, `TRUE` = 0.45), guide = "none") +
      scale_y_continuous(labels = label_comma(), expand = expansion(mult = c(0, 0.05))) +
      scale_x_continuous(breaks = pretty_breaks()) +
      labs(x = "Year", y = expression(Citations ~ yr^{-1}),
           caption = "Current year is partial (lighter bar).")
  })
  output$p_hist <- renderPlot(p_hist(), res = 110)

  p_traj <- reactive({
    met <- input$traj_metric
    obs <- d()$metrics %>% filter(metric == met) %>% mutate(kind = ifelse(origin == "sync", "Observed", "Imported"))
    rec <- reconstruct(d())
    if (!is.null(rec)) rec <- rec %>% filter(metric == met)
    if (met == "citations" && !is.null(d()$pyc) && nrow(d()$pyc) > 0) {
      sch <- d()$pyc %>% arrange(year) %>% filter(year < as.integer(format(Sys.Date(), "%Y"))) %>%
        transmute(snapshot_date = as.Date(sprintf("%d-12-31", year)), source = "scholar",
                  metric = "citations", value = cumsum(citations))
      rec <- bind_rows(rec, sch)
    }
    req(nrow(obs) > 0 || (!is.null(rec) && nrow(rec) > 0))
    p <- ggplot(mapping = aes(snapshot_date, value, colour = source))
    if (!is.null(rec) && nrow(rec) > 0) {
      p <- p + geom_line(data = rec, linetype = "dashed", linewidth = 0.6) +
        geom_point(data = rec, shape = 1, size = 1.6)
    }
    if (nrow(obs) > 0) {
      p <- p + geom_line(data = obs %>% filter(kind == "Observed"), linewidth = 0.8) +
        geom_point(data = obs, aes(shape = kind), size = 1.8)
    }
    int_metric <- met %in% c("citations", "h", "g", "i10", "hc", "hI_norm", "annual_h", "n_papers")
    int_breaks <- function(x) { b <- pretty(x); b[b == round(b)] }
    p + scale_colour_manual(values = src_cols, labels = src_labs, breaks = c("scholar", "openalex")) +
      scale_shape_manual(values = c(Observed = 16, Imported = 17),
                         guide = if (any(obs$kind == "Imported")) "legend" else "none") +
      scale_y_continuous(labels = label_comma(), breaks = if (int_metric) int_breaks else waiver()) +
      labs(x = NULL, y = metric_labels[[met]],
           caption = paste0("Solid: weekly snapshots. Dashed: reconstructed year end values\n",
                            "(OpenAlex per work counts; Scholar profile histogram for citations)."))
  })
  output$p_traj <- renderPlot(p_traj(), res = 110)

  # ---- papers
  output$papers_tbl <- renderDT({
    p <- papers() %>%
      transmute(Title = title, Year = year, Venue = venue, `Author position` =
                  ifelse(is.na(author_position), "", paste0(author_position, " of ", n_authors)),
                Scholar = scholar, OpenAlex = openalex, `Velocity (yr⁻¹)` = round(velocity, 1),
                FWCI = round(fwci, 2), Percentile = round(100 * pct_value, 1),
                `Top 10%` = ifelse(top10 %in% 1, "yes", ""))
    datatable(p, rownames = FALSE, selection = "single", filter = "top",
              options = list(pageLength = 15, scrollX = TRUE, order = list(list(4, "desc")),
                             columnDefs = list(list(className = "dt-center", targets = 1:9))))
  })
  sel <- reactive({
    i <- input$papers_tbl_rows_selected
    req(length(i) == 1)
    papers()[i, ]
  })
  wrap <- function(x, n = 70) paste(strwrap(x, n), collapse = "\n")
  p_paper_gs <- reactive({
    s <- sel()
    h <- d()$ws %>% filter(work_id == s$work_id, source == "scholar") %>% arrange(snapshot_date)
    validate(need(nrow(h) >= 2, "Weekly history builds from the second Scholar run onward."))
    ggplot(h, aes(snapshot_date, citations)) +
      geom_line(colour = src_cols[["scholar"]], linewidth = 0.8) +
      geom_point(colour = src_cols[["scholar"]], size = 1.5) +
      labs(x = NULL, y = "Cumulative citations", title = wrap(s$title))
  })
  p_paper_oa <- reactive({
    s <- sel()
    h <- d()$wyc %>% filter(work_id == s$work_id)
    validate(need(nrow(h) > 0, "No OpenAlex yearly counts for this paper (unmatched or uncited)."))
    ggplot(h, aes(year, citations)) +
      geom_col(fill = src_cols[["openalex"]], width = 0.75) +
      scale_x_continuous(breaks = pretty_breaks()) +
      scale_y_continuous(expand = expansion(mult = c(0, 0.05))) +
      labs(x = "Year", y = expression(Citations ~ yr^{-1}), title = wrap(s$title))
  })
  output$p_paper_gs <- renderPlot(p_paper_gs(), res = 110)
  output$p_paper_oa <- renderPlot(p_paper_oa(), res = 110)

  # ---- h core
  p_hcore <- reactive({
    p <- papers() %>% filter(!is.na(scholar)) %>% arrange(desc(scholar)) %>% mutate(rank = row_number())
    req(nrow(p) > 0)
    h <- h_of(p$scholar)
    p <- p %>% mutate(zone = case_when(rank <= h ~ "In the h core",
                                       scholar >= h - 2 & scholar < h + 1 ~ "Within 3 citations of h + 1",
                                       TRUE ~ "Other"))
    show <- p %>% filter(rank <= max(3 * h, 30))
    ggplot(show, aes(rank, scholar, colour = zone)) +
      geom_abline(slope = 1, intercept = 0, colour = "grey50", linetype = "dashed", linewidth = 0.4) +
      geom_vline(xintercept = h + 0.5, colour = "grey70", linetype = "dotted", linewidth = 0.4) +
      geom_point(size = 2) +
      annotate("text", x = h + 0.5, y = max(show$scholar), label = paste0("h = ", h),
               hjust = -0.15, vjust = 1, size = 3.6) +
      scale_colour_manual(values = c(`In the h core` = src_cols[["scholar"]],
                                     `Within 3 citations of h + 1` = "#E69F00", Other = "grey65"),
                          breaks = c("In the h core", "Within 3 citations of h + 1", "Other")) +
      scale_y_log10(labels = label_comma()) +
      labs(x = "Rank", y = "Citations (log scale)",
           caption = paste0("Dashed line: citations = rank. Dotted line: edge of the h core.\n",
                            "Orange papers need at most 3 more citations to lift h by one, given enough of them."))
  })
  output$p_hcore <- renderPlot(p_hcore(), res = 110)

  # ---- field impact
  p_fwci <- reactive({
    p <- papers() %>% filter(!is.na(fwci), !is.na(year))
    req(nrow(p) > 0)
    nz <- sum(p$fwci <= 0)
    p <- p %>% filter(fwci > 0) %>%
      mutate(tier = case_when(top1 %in% 1 ~ "Top 1%", top10 %in% 1 ~ "Top 10%", TRUE ~ "Other"))
    ggplot(p, aes(year, fwci, colour = tier)) +
      geom_hline(yintercept = 1, colour = "grey40", linewidth = 0.4) +
      geom_jitter(width = 0.15, height = 0, size = 2, alpha = 0.85) +
      scale_y_log10(breaks = c(0.1, 0.3, 1, 3, 10, 30)) +
      scale_colour_manual(values = c(`Top 1%` = "#D55E00", `Top 10%` = "#E69F00", Other = "grey60"),
                          breaks = c("Top 1%", "Top 10%", "Other")) +
      labs(x = "Publication year", y = "FWCI (log scale)",
           caption = paste0("Line at 1 = world average for the subfield, year and type.",
                            if (nz > 0) paste0(" ", nz, " papers with FWCI of 0 not shown.") else ""))
  })
  output$p_fwci <- renderPlot(p_fwci(), res = 110)

  # ---- data quality
  output$runs_tbl <- renderDT({
    r <- d()$runs %>% arrange(desc(run_id)) %>% head(20) %>%
      select(run_id, snapshot_date, origin, scholar_status, openalex_status, n_scholar, n_openalex, message)
    datatable(r, rownames = FALSE, options = list(dom = "tp", pageLength = 10, scrollX = TRUE))
  })
  output$flags_tbl <- renderDT({
    datatable(d()$flags %>% select(kind, gs_id, detail), rownames = FALSE,
              options = list(pageLength = 10, scrollX = TRUE))
  })
  output$unmatched_tbl <- renderDT({
    u <- d()$works %>% filter(!is.na(gs_id), is.na(openalex_id), excluded == 0, is.na(merged_into)) %>%
      select(gs_id, year, title)
    datatable(u, rownames = FALSE, options = list(pageLength = 10, scrollX = TRUE))
  })

  # ---- PNG downloads, 7 x 4.5 in at 300 dpi
  dl <- function(id, plot_r) {
    output[[paste0("dl_", id)]] <- downloadHandler(
      filename = function() sprintf("%s_pubtrack-%s_DATA.png", Sys.Date(), sub("^p_", "", id)),
      content = function(file) save_png(file, plot_r())
    )
  }
  dl("p_hist", p_hist); dl("p_traj", p_traj); dl("p_paper_gs", p_paper_gs)
  dl("p_paper_oa", p_paper_oa); dl("p_hcore", p_hcore); dl("p_fwci", p_fwci)
}

shinyApp(ui, server)
