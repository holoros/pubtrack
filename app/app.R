# pubtrack: bibliometric analytics for tracked authors (Google Scholar and OpenAlex).
# Runs locally (shiny::runApp("app")) and in the browser through Shinylive on GitHub Pages.
# Data: data/bundle.rds, built from the committed CSV store by build_bundle.R.

suppressPackageStartupMessages({
  library(shiny)
  library(bslib)
  library(plotly)
  library(DT)
  library(dplyr)
  library(tidyr)
  library(ggplot2)
  library(scales)
  library(tibble)
})
if (requireNamespace("bsicons", quietly = TRUE)) library(bsicons) else bs_icon <- function(name, ...) NULL
if (!exists("build_bundle")) for (f in sort(list.files("R", pattern = "\\.R$", full.names = TRUE))) source(f)

B <- load_bundle(".")
authors <- B$authors |> arrange(group != "self", name)
author_choices <- setNames(authors$author_id, authors$name)
traj_choices <- setNames(names(metric_labels), metric_labels)

theme <- bs_theme(
  version = 5, bg = "#ffffff", fg = pt_col$ink, primary = pt_col$crsf_green, secondary = pt_col$ink_muted,
  success = pt_col$ok, warning = pt_col$warn, danger = pt_col$alert, info = pt_col$crsf_sky,
  base_font = font_google("Source Sans 3", local = FALSE),
  heading_font = font_google("Libre Franklin", wght = c(700, 800), local = FALSE),
  code_font = font_google("IBM Plex Mono", local = FALSE),
  "border-radius" = "8px", "card-border-color" = pt_col$rule, "navbar-bg" = pt_col$crsf_green
) |> bs_add_rules(sprintf("
  .navbar { border-bottom: 0; }
  .navbar .navbar-brand { font-family: 'Libre Franklin', sans-serif; font-weight: 800; letter-spacing: .02em; }
  .card { box-shadow: none; }
  .card-header { font-family: 'Libre Franklin', sans-serif; font-weight: 700; background: %s; border-bottom: 1px solid %s; }
  .bslib-value-box .value-box-title { font-weight: 700; }
  .bslib-value-box .value-box-value { font-family: 'Libre Franklin', sans-serif; font-weight: 800; font-variant-numeric: tabular-nums; }
  .vb-sub { font-size: .85rem; opacity: .85; }
  .stmt { font-size: 1.02rem; line-height: 1.5; background: %s; border: 1px solid %s; border-radius: 6px; padding: 14px 16px; }
  .eyebrow { text-transform: uppercase; letter-spacing: .08em; font-size: .75rem; font-weight: 700; color: %s; }
  table.dataTable td, table.dataTable th { text-align: center; vertical-align: middle; }
  table.dataTable td.dt-left { text-align: left; }
  .report-h { font-family: 'Libre Franklin', sans-serif; font-weight: 800; }
  @media print {
    .navbar, .bslib-sidebar-layout > .sidebar, .collapse-toggle, .no-print, .nav-tabs { display: none !important; }
    .bslib-sidebar-layout { grid-template-columns: 1fr !important; display: block !important; }
    .card { break-inside: avoid; border: 0 !important; }
    body { background: white !important; }
  }",
  pt_col$sunken, pt_col$rule, pt_col$sunken, pt_col$rule, pt_col$crsf_green))

pcard <- function(title, ..., height = NULL, footer = NULL) {
  card(full_screen = TRUE, height = height, card_header(title), card_body(..., padding = 8),
       if (!is.null(footer)) card_footer(footer))
}
copy_btn <- function(target) {
  tags$button(class = "btn btn-sm btn-outline-primary no-print", type = "button",
              onclick = sprintf("navigator.clipboard.writeText(document.getElementById('%s').innerText).then(()=>{this.innerText='Copied';setTimeout(()=>this.innerText='Copy',1500)})", target),
              "Copy")
}

ui <- page_navbar(
  title = "pubtrack", theme = theme, fillable = FALSE, window_title = "pubtrack bibliometrics",
  # Load plotly.js with the page so the first widgets never render before the library (Shinylive race).
  header = div(style = "display:none", plotly::plotly_empty(height = 10)),
  sidebar = sidebar(
    width = 290, open = "desktop",
    div(class = "eyebrow", "Focus"),
    selectInput("focal", NULL, author_choices, selected = author_choices[1]),
    div(class = "eyebrow", "Compare with"),
    selectizeInput("compare", NULL, author_choices, selected = author_choices, multiple = TRUE,
                   options = list(plugins = list("remove_button"))),
    div(class = "eyebrow", "Citation source"),
    radioButtons("source", NULL, c("Google Scholar" = "scholar", "OpenAlex" = "openalex"), inline = TRUE),
    hr(),
    div(class = "small text-muted",
        HTML(paste0("Data built ", format(B$built, "%d %b %Y"), ". Scholar via profile snapshots; field impact via OpenAlex.<br>",
                    "<a href='https://github.com/holoros/pubtrack' target='_blank'>Source and data</a>")))
  ),

  nav_panel("Overview", icon = bs_icon("speedometer2"),
    uiOutput("vboxes"),
    layout_columns(col_widths = c(7, 5),
      pcard("Publication portfolio", plotlyOutput("p_portfolio", height = "430px")),
      pcard("Citations per year", plotlyOutput("p_hist", height = "430px"))),
    layout_columns(col_widths = c(6, 6),
      pcard("h index outlook", plotlyOutput("p_fan_h", height = "360px")),
      pcard("Citation outlook", plotlyOutput("p_fan_c", height = "360px")))
  ),

  nav_panel("Compare", icon = bs_icon("people"),
    uiOutput("solo_hint"),
    pcard("Profile across the selected authors", plotlyOutput("p_heat", height = "560px")),
    layout_columns(col_widths = c(6, 6),
      pcard("Citations by academic age", plotlyOutput("p_career_c", height = "380px")),
      pcard("h index by academic age", plotlyOutput("p_career_h", height = "380px"))),
    layout_columns(col_widths = c(6, 6),
      pcard("Projected h in 12 months", plotlyOutput("p_proj", height = "340px")),
      pcard("Citation concentration", plotlyOutput("p_lorenz", height = "340px"))),
    layout_columns(col_widths = c(7, 5),
      pcard("Output per year", plotlyOutput("p_output", height = "340px")),
      pcard("Authorship roles", plotlyOutput("p_roles", height = "340px"))),
    pcard("Indices table", DTOutput("compare_tbl"),
          footer = downloadButton("dl_compare", "CSV", class = "btn-sm btn-outline-secondary"))
  ),

  nav_panel("Trajectory", icon = bs_icon("graph-up"),
    card(full_screen = TRUE,
      card_header(class = "d-flex justify-content-between align-items-center", "Index over time",
                  div(class = "d-flex gap-2 align-items-center",
                      selectInput("traj_metric", NULL, traj_choices, selected = "h", width = "230px"),
                      checkboxInput("traj_recon", "Reconstructed years", TRUE))),
      plotlyOutput("p_traj", height = "480px")),
    pcard("Change over 4 and 52 weeks", DTOutput("delta_tbl"))
  ),

  nav_panel("Publications", icon = bs_icon("journal-text"),
    pcard("Papers (select a row)", DTOutput("papers_tbl"),
          footer = downloadButton("dl_papers", "CSV", class = "btn-sm btn-outline-secondary")),
    uiOutput("paper_panel")
  ),

  nav_panel("Forecast", icon = bs_icon("binoculars"),
    layout_columns(col_widths = c(6, 6),
      pcard("h index, three years", plotlyOutput("p_fan_h2", height = "380px")),
      pcard("Total citations, three years", plotlyOutput("p_fan_c2", height = "380px"))),
    uiOutput("bt_card"),
    layout_columns(col_widths = c(6, 6),
      pcard("Closest to lifting h", DTOutput("frontier_tbl")),
      pcard("Fastest risers, next 12 months", DTOutput("risers_tbl"))),
    pcard("h core", plotlyOutput("p_hcore", height = "420px"))
  ),

  nav_panel("Report", icon = bs_icon("file-earmark-text"),
    layout_columns(col_widths = c(4, 8), class = "no-print",
      card(card_header("Report settings"), card_body(
        selectInput("rep_author", "Author", author_choices),
        selectizeInput("rep_paper", "Publication (optional)", choices = NULL),
        radioButtons("rep_rank", "Highlight publications by",
                     c("Citations" = "citations", "Field weighted impact" = "fwci", "Current momentum" = "rate"), inline = FALSE),
        sliderInput("rep_ntop", "Publications named in the statement", 1, 5, 3, step = 1),
        checkboxInput("rep_fc", "Include the forecast sentence", TRUE),
        div(class = "d-flex gap-2",
            tags$button(class = "btn btn-primary", onclick = "window.print()", bs_icon("printer"), " Print or save PDF"),
            uiOutput("static_link", inline = TRUE)))),
      card(card_header(class = "d-flex justify-content-between align-items-center",
                       "Impact statement for letters and emails", copy_btn("stmt_author")),
           card_body(div(id = "stmt_author", class = "stmt", textOutput("stmt_author_txt", inline = TRUE))),
           uiOutput("paper_stmt_card"))
    ),
    uiOutput("report_body")
  ),

  nav_panel("Data", icon = bs_icon("database-check"),
    layout_columns(col_widths = c(12, 6, 6),
      pcard("Recent runs", DTOutput("runs_tbl")),
      pcard("Flags on each author's latest run", DTOutput("flags_tbl")),
      pcard("Works shared between tracked authors", DTOutput("shared_tbl"))),
    card(card_header("Methods"), card_body(HTML(methods_html())))
  ),
  nav_spacer(),
  nav_item(tags$span(class = "navbar-text small", style = "color:#c8e2f6", textOutput("asof", inline = TRUE)))
)

server <- function(input, output, session) {
  fa <- reactive({ req(input$focal %in% authors$author_id); input$focal })
  ra <- reactive({ req(input$rep_author %in% authors$author_id); input$rep_author })
  ids <- reactive({ u <- unique(c(fa(), input$compare)); u[u %in% authors$author_id] })
  cm <- reactive(B$compare[B$compare$author_id == fa(), ])
  focal_papers <- reactive(B$papers[B$papers$author_id == fa() & !is.na(B$papers$unit_citations), ])

  output$asof <- renderText(paste("As of", format(cm()$snapshot_date, "%d %b %Y")))

  # ---- overview
  delta <- function(aid, unit, metric, days) {
    m <- B$metrics |> filter(author_id == aid, source == unit, metric == !!metric, origin == "sync") |> arrange(snapshot_date)
    if (nrow(m) < 2) return(NA)
    now <- tail(m, 1)
    old <- m |> filter(snapshot_date <= now$snapshot_date - days) |> tail(1)
    if (nrow(old) == 0) NA else now$value - old$value
  }
  output$vboxes <- renderUI({
    x <- cm(); u <- x$unit
    sub <- function(metric) {
      d4 <- delta(x$author_id, u, metric, 28); d52 <- delta(x$author_id, u, metric, 364)
      txt <- c(if (!is.na(d4)) paste0(fmt_signed(d4), " in 4 weeks"), if (!is.na(d52)) paste0(fmt_signed(d52), " in 52 weeks"))
      div(class = "vb-sub", if (length(txt)) paste(txt, collapse = " · ") else "History builds weekly")
    }
    layout_column_wrap(width = "200px", fill = FALSE,
      value_box(paste0("Citations (", .unit_lab(u), ")"), fmt_n(x$citations), sub("citations"),
                showcase = bs_icon("quote"), theme = value_box_theme(bg = "#ffffff", fg = pt_col$ink)),
      value_box("h index", x$h, sub("h"), showcase = bs_icon("bar-chart-steps"),
                theme = value_box_theme(bg = "#ffffff", fg = pt_col$ink)),
      value_box("Projected h, 12 months",
                if (is.na(x$proj_h_12m) || !isTRUE(x$forecast_ok)) "n/a" else round(x$proj_h_12m),
                div(class = "vb-sub", if (!isTRUE(x$forecast_ok)) "Forecast check not passed" else
                  paste0("80% interval ", x$proj_h_12m_lo, " to ", x$proj_h_12m_hi)),
                showcase = bs_icon("binoculars"), theme = value_box_theme(bg = pt_col$crsf_green, fg = "#ffffff")),
      value_box("Median FWCI", if (is.na(x$fwci_median)) "n/a" else formatC(x$fwci_median, format = "f", digits = 2),
                div(class = "vb-sub", if (!is.na(x$top10_share)) paste0(fmt_pct(x$top10_share), " of papers in top 10%")),
                showcase = bs_icon("globe2"), theme = value_box_theme(bg = "#ffffff", fg = pt_col$ink)),
      value_box(paste("Citations in", as.integer(format(x$snapshot_date, "%Y")) - 1), fmt_n(x$cites_last_full_year),
                div(class = "vb-sub", if (!is.na(x$growth_yoy)) paste0(fmt_signed(100 * x$growth_yoy), "% on the year before")),
                showcase = bs_icon("calendar3"), theme = value_box_theme(bg = "#ffffff", fg = pt_col$ink))
    )
  })
  output$p_portfolio <- renderPlotly(pt_plotly(plot_portfolio(B, fa())))
  output$p_hist <- renderPlotly(pt_plotly(plot_hist(B, fa())))
  output$p_fan_h <- renderPlotly(pt_plotly(plot_fan(B, fa(), "h")))
  output$p_fan_c <- renderPlotly(pt_plotly(plot_fan(B, fa(), "citations_total")))

  # ---- compare
  output$p_heat <- renderPlotly(pt_plotly(plot_heat(B, ids(), fa())) |> plotly::layout(xaxis = list(side = "top")))
  output$solo_hint <- renderUI({
    if (length(ids()) > 1) return(NULL)
    div(class = "alert alert-light border mb-3",
        "Only one author is selected. Add peers to authors.yml on GitHub (id, name, ORCID or OpenAlex id, optional Scholar id); ",
        "the next refresh fetches them and this view ranks every metric across the set.")
  })
  output$p_career_c <- renderPlotly(pt_plotly(plot_career(B, ids(), "citations", fa())))
  output$p_career_h <- renderPlotly(pt_plotly(plot_career(B, ids(), "h", fa())))
  output$p_proj <- renderPlotly(pt_plotly(plot_projected_h(B, ids(), fa())))
  output$p_lorenz <- renderPlotly(pt_plotly(plot_lorenz(B, ids(), fa())))
  output$p_output <- renderPlotly(pt_plotly(plot_output(B, ids(), fa())))
  output$p_roles <- renderPlotly(pt_plotly(plot_roles(B, ids(), fa())))
  compare_view <- reactive({
    B$compare |> filter(author_id %in% ids()) |>
      transmute(Author = name, Source = src_labs[unit], Papers = n_papers, Citations = citations, h, g, i10,
                `m quotient` = round(m_quotient, 2), hc, `hI norm` = hI_norm, hm = round(hm, 1), `Median FWCI` = round(fwci_median, 2),
                `Top 10%` = fmt_pct(top10_share), `Citations last year` = cites_last_full_year,
                `Growth` = fmt_pct(growth_yoy), `Gini` = round(gini, 2), `Projected h` = proj_h_12m,
                `h 80% interval` = paste(proj_h_12m_lo, "to", proj_h_12m_hi))
  })
  output$compare_tbl <- renderDT(datatable(compare_view(), rownames = FALSE, options = list(dom = "t", scrollX = TRUE, pageLength = 50)))
  output$dl_compare <- downloadHandler(function() sprintf("%s_pubtrack-compare_DATA.csv", Sys.Date()),
                                       function(f) utils::write.csv(compare_view(), f, row.names = FALSE))

  # ---- trajectory
  output$p_traj <- renderPlotly(pt_plotly(plot_trajectory(B, ids(), input$traj_metric, input$source, fa(), input$traj_recon)))
  output$delta_tbl <- renderDT({
    d <- lapply(ids(), function(a) {
      x <- B$compare[B$compare$author_id == a, ]
      tibble(Author = x$name, Metric = metric_labels[[input$traj_metric]] %||% input$traj_metric,
             Now = { v <- B$metrics |> filter(author_id == a, source == input$source, metric == input$traj_metric, origin == "sync") |>
                       arrange(snapshot_date) |> tail(1); if (nrow(v)) round(v$value, 2) else NA },
             `4 weeks` = round(delta(a, input$source, input$traj_metric, 28), 2),
             `52 weeks` = round(delta(a, input$source, input$traj_metric, 364), 2))
    })
    datatable(bind_rows(d), rownames = FALSE, options = list(dom = "t"))
  })

  # ---- publications
  pview <- reactive({
    focal_papers() |> arrange(desc(unit_citations)) |>
      transmute(work_id, Title = title, Year = year, Venue = clean_venue(venue), Role = role, Citations = unit_citations,
                OpenAlex = openalex, FWCI = round(fwci, 2), Percentile = round(100 * pct_value, 1),
                `Velocity yr⁻¹` = round(velocity, 1), `Next 12 mo` = round(f12_med),
                `80% interval` = ifelse(is.na(f12_lo), "", paste(round(f12_lo), "to", round(f12_hi))),
                `Trend yr⁻¹` = ifelse(is.na(trend_pct), "", fmt_pct(trend_pct)))
  })
  output$papers_tbl <- renderDT({
    datatable(select(pview(), -work_id), rownames = FALSE, selection = "single", filter = "top",
              options = list(pageLength = 12, scrollX = TRUE, order = list(list(4, "desc")),
                             columnDefs = list(list(className = "dt-left", targets = c(0, 2)),
                                               list(width = "34%", targets = 0))))
  })
  output$dl_papers <- downloadHandler(function() sprintf("%s_%s-papers_DATA.csv", Sys.Date(), fa()),
                                      function(f) utils::write.csv(pview(), f, row.names = FALSE))
  sel_wid <- reactive({ i <- input$papers_tbl_rows_selected; req(length(i) == 1); pview()$work_id[i] })
  output$paper_panel <- renderUI({
    wid <- sel_wid()
    layout_columns(col_widths = c(6, 6, 12),
      pcard("Weekly counts", plotlyOutput("p_pw", height = "320px")),
      pcard("Citations per year and forecast", plotlyOutput("p_pa", height = "320px")),
      card(card_header(class = "d-flex justify-content-between align-items-center", "Publication statement", copy_btn("stmt_pub")),
           card_body(div(id = "stmt_pub", class = "stmt", paper_statement(B, fa(), wid)))))
  })
  output$p_pw <- renderPlotly(pt_plotly(plot_paper_weekly(B, fa(), sel_wid())))
  output$p_pa <- renderPlotly(pt_plotly(plot_paper_annual(B, fa(), sel_wid())))

  # ---- forecast
  output$p_fan_h2 <- renderPlotly(pt_plotly(plot_fan(B, fa(), "h")))
  output$p_fan_c2 <- renderPlotly(pt_plotly(plot_fan(B, fa(), "citations_total")))
  output$frontier_tbl <- renderDT({
    f <- h_frontier(focal_papers())
    datatable(transmute(f, Rank = rank, Title = title, Citations = unit_citations, Needed = needed,
                        `Next 12 mo` = round(f12_med)),
              rownames = FALSE, options = list(dom = "t", pageLength = 15, columnDefs = list(list(className = "dt-left", targets = 1))))
  })
  output$risers_tbl <- renderDT({
    r <- focal_papers() |> arrange(desc(f12_med)) |> head(10)
    datatable(transmute(r, Title = title, Year = year, Citations = unit_citations, `Next 12 mo` = round(f12_med),
                        `80% interval` = paste(round(f12_lo), "to", round(f12_hi)), Method = fc_method),
              rownames = FALSE, options = list(dom = "t", columnDefs = list(list(className = "dt-left", targets = 0))))
  })
  output$bt_card <- renderUI({
    bt <- B$backtest[B$backtest$author_id == fa(), ]
    x <- cm()
    if (is.null(bt) || nrow(bt) == 0) {
      return(card(card_body(class = "small", "Forecast check unavailable: fewer than ten papers with two or more years of OpenAlex counts.")))
    }
    ok <- isTRUE(x$forecast_ok)
    card(class = if (ok) "border-success" else "border-danger",
         card_header(if (ok) "Forecast check passed" else "Forecast check failed: projections are indicative only"),
         card_body(p(sprintf(paste0("Refit with OpenAlex counts through %d, the model predicted %s citations to these %d papers in %d ",
                                    "(80%% interval %s to %s). OpenAlex recorded %s, a ratio of %.2f. Per paper 80%% intervals ",
                                    "covered %s of observed counts."),
                             bt$target_year - 1, fmt_n(bt$pred_q50), bt$n_papers, bt$target_year, fmt_n(bt$pred_q10),
                             fmt_n(bt$pred_q90), fmt_n(bt$observed), bt$ratio, fmt_pct(bt$paper_cov80)))))
  })
  output$p_hcore <- renderPlotly(pt_plotly(plot_hcore(B, fa())))

  # ---- report
  observeEvent(input$focal, updateSelectInput(session, "rep_author", selected = input$focal))
  observeEvent(input$rep_author, {
    p <- B$papers |> filter(author_id == ra(), !is.na(unit_citations)) |> arrange(desc(unit_citations))
    ch <- if (nrow(p)) setNames(as.character(p$work_id), paste0(substr(p$title, 1, 90), " (", p$year, ")")) else character()
    updateSelectizeInput(session, "rep_paper", choices = c("None" = "", ch),
                         server = FALSE)
  })
  stmt_a <- reactive(impact_statement(B, ra(), input$rep_rank, input$rep_ntop, input$rep_fc))
  output$stmt_author_txt <- renderText(stmt_a())
  output$paper_stmt_card <- renderUI({
    req(nzchar(input$rep_paper %||% ""))
    tagList(hr(), div(class = "d-flex justify-content-between align-items-center mb-2",
                      tags$strong("Publication statement"), copy_btn("stmt_rep_pub")),
            div(id = "stmt_rep_pub", class = "stmt", paper_statement(B, ra(), as.integer(input$rep_paper))))
  })
  output$static_link <- renderUI({
    if (!nzchar(B$site_url %||% "")) return(NULL)
    tags$a(class = "btn btn-outline-primary", target = "_blank", href = paste0(B$site_url, "reports/", ra(), ".html"),
           bs_icon("box-arrow-up-right"), " Full HTML report")
  })
  output$report_body <- renderUI({
    a <- ra(); x <- B$compare[B$compare$author_id == a, ]
    wid <- if (nzchar(input$rep_paper %||% "")) as.integer(input$rep_paper) else NA
    tagList(
      div(class = "mt-3 mb-2", div(class = "eyebrow", "Impact report"),
          h2(class = "report-h", x$name),
          p(class = "text-muted", paste0("Data as of ", format(x$snapshot_date, "%d %B %Y"), ". Citation counts from ", .unit_lab(x$unit),
                                         "; field impact from OpenAlex. ", if (nzchar(x$orcid)) paste0("ORCID ", x$orcid, ".") else ""))),
      div(class = "stmt mb-3", stmt_a()),
      card(card_header("Key indices"), card_body(tableOutput("rep_tbl"))),
      layout_columns(col_widths = c(6, 6),
        card(card_header("Trajectory"), card_body(plotOutput("rep_traj", height = "320px"))),
        card(card_header("Citations per year"), card_body(plotOutput("rep_hist", height = "320px")))),
      layout_columns(col_widths = c(6, 6),
        card(card_header("h index outlook"), card_body(plotOutput("rep_fan_h", height = "320px"))),
        card(card_header("Citation outlook"), card_body(plotOutput("rep_fan_c", height = "320px")))),
      card(card_header("Most impactful publications"), card_body(tableOutput("rep_top"))),
      if (!is.na(wid)) tagList(
        h3(class = "report-h mt-4", "Publication detail"),
        div(class = "stmt mb-3", paper_statement(B, a, wid)),
        layout_columns(col_widths = c(6, 6),
          card(card_header("Weekly counts"), card_body(plotOutput("rep_pw", height = "300px"))),
          card(card_header("Citations per year and forecast"), card_body(plotOutput("rep_pa", height = "300px")))))
    )
  })
  output$rep_tbl <- renderTable({
    x <- B$compare[B$compare$author_id == ra(), ]
    tibble(Metric = c("Papers", "Citations", "h index", "g index", "i10 index", "m quotient", "Contemporary h", "hI norm",
                      "Papers per career year", "Citations per career year", "Citations per year, last 5 complete years",
             "h5", "Citations per paper per year (median)", "Papers published, last 5 complete years",
             "h of papers from last 5 years", "Median FWCI", "Share in top 10%", "Citations last full year", "Projected h in 12 months (80% interval)",
                      "Projected new citations in 12 months (80% interval)"),
           Value = c(fmt_n(x$n_papers), fmt_n(x$citations), x$h, x$g, x$i10, formatC(x$m_quotient, format = "f", digits = 2),
                     fmt_n(x$hc), fmt_n(x$hI_norm),
            formatC(x$papers_per_year, format = "f", digits = 1), fmt_n(x$cites_per_year), fmt_n(x$cites_per_year_5y),
            fmt_n(x$h5), formatC(x$cites_per_paper_year_median, format = "f", digits = 2), fmt_n(x$papers_5y),
            fmt_n(x$h_recent), formatC(x$fwci_median, format = "f", digits = 2), fmt_pct(x$top10_share),
                     fmt_n(x$cites_last_full_year), paste0(x$proj_h_12m, " (", x$proj_h_12m_lo, " to ", x$proj_h_12m_hi, ")"),
                     paste0(fmt_n(x$proj_cites_12m), " (", fmt_n(x$proj_cites_12m_lo), " to ", fmt_n(x$proj_cites_12m_hi), ")")))
  }, striped = FALSE, align = "lc", width = "100%")
  output$rep_top <- renderTable({
    top_papers(B, ra(), input$rep_rank, 10) |>
      transmute(Title = title, Year = as.integer(year), Venue = clean_venue(venue), Citations = fmt_n(unit_citations),
                FWCI = formatC(fwci, format = "f", digits = 2), `Next 12 months` = paste0(round(f12_med), " (", round(f12_lo), " to ", round(f12_hi), ")"))
  }, align = "lccccc", width = "100%")
  output$rep_traj <- renderPlot(wrap_caption(plot_trajectory(B, ra(), "h", B$compare$unit[B$compare$author_id == ra()], ra()) +
                                  ggplot2::theme(legend.position = "none")), res = 110)
  output$rep_hist <- renderPlot(wrap_caption(plot_hist(B, ra())), res = 110)
  output$rep_fan_h <- renderPlot(wrap_caption(plot_fan(B, ra(), "h")), res = 110)
  output$rep_fan_c <- renderPlot(wrap_caption(plot_fan(B, ra(), "citations_total")), res = 110)
  output$rep_pw <- renderPlot(wrap_caption(plot_paper_weekly(B, ra(), as.integer(input$rep_paper))), res = 110)
  output$rep_pa <- renderPlot(wrap_caption(plot_paper_annual(B, ra(), as.integer(input$rep_paper))), res = 110)

  # ---- data
  output$runs_tbl <- renderDT(datatable(
    B$runs |> arrange(desc(run_id)) |> head(200) |>
      select(run_id, author_id, snapshot_date, origin, scholar_status, openalex_status, serpapi_calls, n_scholar, n_openalex, message),
    rownames = FALSE, filter = "top", options = list(pageLength = 10, scrollX = TRUE)))
  output$flags_tbl <- renderDT(datatable(
    if (is.null(B$flags)) tibble(kind = character()) else B$flags |> select(author_id, kind, gs_id, detail),
    rownames = FALSE, options = list(pageLength = 10, scrollX = TRUE)))
  output$shared_tbl <- renderDT(datatable(
    B$shared |> filter(author_id_a < author_id_b) |>
      transmute(`Author A` = .anames(B, author_id_a), `Author B` = .anames(B, author_id_b), `Shared works` = n_shared),
    rownames = FALSE, options = list(dom = "tp")))
}

shinyApp(ui, server)
