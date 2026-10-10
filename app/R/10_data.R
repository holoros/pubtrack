# Read the committed CSV store and derive everything the app and reports show.
# build_bundle() runs once per refresh (GitHub Actions) and is saved as data/bundle.rds, so the
# browser (Shinylive) only filters and draws. Every number traces to a run in data/runs.csv.

h_of <- function(x) { x <- sort(x[!is.na(x)], decreasing = TRUE); sum(x >= seq_along(x)) }
g_of <- function(x) { x <- sort(x[!is.na(x)], decreasing = TRUE); sum(cumsum(x) >= seq_along(x)^2) }
gini_of <- function(x) {
  x <- sort(x[!is.na(x)]); n <- length(x)
  if (n < 2 || sum(x) == 0) return(NA_real_)
  sum((2 * seq_len(n) - n - 1) * x) / (n * sum(x))
}

slope_per_year <- function(d, y) {
  t <- as.numeric(d) / 365.25
  if (length(t) < 2 || stats::var(t) == 0) return(NA_real_)
  sum((t - mean(t)) * (y - mean(y))) / sum((t - mean(t))^2)
}

read_store <- function(dir) {
  rd <- function(t) {
    f <- file.path(dir, paste0(t, ".csv"))
    if (!file.exists(f)) return(NULL)
    utils::read.csv(f, stringsAsFactors = FALSE, na.strings = "", check.names = FALSE, encoding = "UTF-8")
  }
  tabs <- c("runs", "works", "work_snapshots", "work_year_counts", "profile_snapshots", "profile_year_counts",
            "work_impact", "metrics", "overrides", "flags")
  s <- setNames(lapply(tabs, rd), tabs)
  if (is.null(s$runs) || nrow(s$runs) == 0) stop("No runs in ", dir, ". Run `pubtrack sync` first.")
  s$runs$snapshot_date <- as.Date(s$runs$snapshot_date)
  s
}

read_authors <- function(path, ids) {
  meta <- NULL
  if (!is.null(path) && file.exists(path) && requireNamespace("yaml", quietly = TRUE)) {
    y <- yaml::read_yaml(path)
    meta <- dplyr::bind_rows(lapply(y$authors, function(a) tibble::tibble(
      author_id = as.character(a$id), name = as.character(a$name %||% a$id),
      group = as.character(a$group %||% ""), scholar_id = as.character(a$scholar_id %||% ""),
      orcid = as.character(a$orcid %||% ""), openalex_id = as.character(a$openalex_id %||% ""),
      cfg_first_year = as.integer(a$first_year %||% 0L))))
  }
  missing <- setdiff(ids, meta$author_id)
  if (length(missing)) {
    meta <- dplyr::bind_rows(meta, tibble::tibble(author_id = missing, name = missing, group = "", scholar_id = "",
                                                  orcid = "", openalex_id = "", cfg_first_year = 0L))
  }
  meta
}
`%||%` <- function(a, b) if (is.null(a) || length(a) == 0) b else a

last_ok_run <- function(runs, aid, src) {
  r <- runs[runs$author_id == aid & runs[[paste0(src, "_status")]] %in% "ok", ]
  if (nrow(r) == 0) return(NA_integer_)
  r$run_id[order(r$snapshot_date, r$run_id, decreasing = TRUE)][1]
}

# Per paper table for one author at the latest successful run of each source, merged duplicates folded in.
author_papers <- function(s, aid, rs, ro) {
  w <- s$works[s$works$author_id == aid, ]
  ws <- s$work_snapshots
  cur <- function(src, run) {
    if (is.na(run)) return(tibble::tibble(work_id = integer(), citations = integer()))
    x <- ws[ws$source == src & ws$run_id == run, c("work_id", "citations")]
    merged <- w[!is.na(w$merged_into) & w$excluded == 0, c("work_id", "merged_into")]
    x |> dplyr::left_join(merged, by = "work_id") |>
      dplyr::mutate(work_id = dplyr::coalesce(merged_into, work_id)) |>
      dplyr::group_by(work_id) |> dplyr::summarise(citations = sum(citations), .groups = "drop")
  }
  runs_a <- s$runs[s$runs$author_id == aid & s$runs$origin == "sync", c("run_id", "snapshot_date")]
  snap_end <- if (nrow(runs_a)) max(runs_a$snapshot_date) else Sys.Date()
  vel <- ws |> dplyr::filter(source == "scholar", work_id %in% w$work_id) |>
    dplyr::inner_join(runs_a, by = "run_id") |>
    dplyr::filter(snapshot_date > snap_end - 365) |>
    dplyr::group_by(work_id) |> dplyr::filter(dplyr::n() >= 8) |>
    dplyr::summarise(velocity = slope_per_year(snapshot_date, citations), .groups = "drop")
  imp <- if (is.na(ro) || is.null(s$work_impact)) {
    tibble::tibble(work_id = integer(), fwci = numeric(), pct_value = numeric(), top10 = integer(), top1 = integer())
  } else s$work_impact[s$work_impact$run_id == ro, c("work_id", "fwci", "pct_value", "top10", "top1")]
  w |>
    dplyr::filter(excluded == 0, is.na(merged_into)) |>
    dplyr::left_join(dplyr::rename(cur("scholar", rs), scholar = citations), by = "work_id") |>
    dplyr::left_join(dplyr::rename(cur("openalex", ro), openalex = citations), by = "work_id") |>
    dplyr::left_join(vel, by = "work_id") |>
    dplyr::left_join(imp, by = "work_id") |>
    dplyr::filter(!is.na(scholar) | !is.na(openalex)) |>
    dplyr::mutate(author_id = aid,
                  role = dplyr::case_when(is.na(n_authors) | is.na(author_position) ~ "Unknown",
                                          n_authors == 1 ~ "Sole", author_position == 1 ~ "First",
                                          is_last_author %in% 1 ~ "Last", TRUE ~ "Middle"))
}

# OpenAlex reconstruction of year end indices: c_i(Y) = cited_by_count now minus citations after Y.
# Exact within the ten year window OpenAlex keeps per work; drawn dashed, never as observations.
reconstruct <- function(s, aid, ro) {
  if (is.na(ro)) return(NULL)
  w <- s$works[s$works$author_id == aid & s$works$excluded == 0 & is.na(s$works$merged_into), c("work_id", "year")]
  now <- s$work_snapshots[s$work_snapshots$source == "openalex" & s$work_snapshots$run_id == ro, c("work_id", "citations")]
  now <- dplyr::inner_join(now, w, by = "work_id")
  wyc <- s$work_year_counts[s$work_year_counts$source == "openalex" & s$work_year_counts$work_id %in% w$work_id, ]
  cy <- as.integer(format(s$runs$snapshot_date[s$runs$run_id == ro], "%Y"))
  dplyr::bind_rows(lapply(seq(cy - 9, cy - 1), function(Y) {
    after <- wyc |> dplyr::filter(year > Y) |> dplyr::group_by(work_id) |>
      dplyr::summarise(a = sum(citations), .groups = "drop")
    cc <- now |> dplyr::filter(!is.na(year), year <= Y) |> dplyr::left_join(after, by = "work_id") |>
      dplyr::mutate(c = pmax(citations - dplyr::coalesce(a, 0L), 0)) |> dplyr::pull(c)
    tibble::tibble(author_id = aid, snapshot_date = as.Date(sprintf("%d-12-31", Y)), year = Y, source = "openalex",
                   metric = c("citations", "h", "g", "i10", "n_papers"),
                   value = c(sum(cc), h_of(cc), g_of(cc), sum(cc >= 10), length(cc)))
  }))
}

metric_labels <- c(
  citations = "Total citations", h = "h index", g = "g index", i10 = "i10 index",
  m_quotient = "m quotient", e = "e index", hc = "Contemporary h (hc)", hI_norm = "hI norm",
  hI_annual = "hI annual", annual_h = "Annual h", awcr = "AWCR", aw = "AW index", awcr_pa = "AWCRpA",
  fwci_median = "Median FWCI", fwci_mean = "Mean FWCI", top10_share = "Share in top 10%",
  top1_share = "Share in top 1%", velocity_per_year = "Citation velocity (per year)",
  interim_rate_per_year = "Interim rate (per year)", n_papers = "Papers", academic_age = "Academic age"
)

build_bundle <- function(store_dir, authors_yml = NULL, nsim = 2000, seed = 20261010, site_url = "") {
  s <- read_store(store_dir)
  ids <- unique(s$runs$author_id)
  meta <- read_authors(authors_yml, ids)
  runs <- s$runs
  has_ok <- unique(runs$author_id[runs$scholar_status %in% "ok" | runs$openalex_status %in% "ok"])
  meta <- meta[meta$author_id %in% intersect(ids, has_ok), ]
  if (nrow(meta) == 0) stop("No author has a successful run yet.")
  metrics <- s$metrics |> dplyr::inner_join(runs[, c("run_id", "author_id", "snapshot_date", "origin")], by = "run_id")

  per <- lapply(meta$author_id, function(aid) {
    rs <- last_ok_run(runs, aid, "scholar")
    ro <- last_ok_run(runs, aid, "openalex")
    snap <- max(runs$snapshot_date[runs$author_id == aid & runs$run_id %in% c(rs, ro)], na.rm = TRUE)
    p <- author_papers(s, aid, rs, ro)
    unit <- if (!is.na(rs)) "scholar" else "openalex"
    m_now <- function(src, run) {
      if (is.na(run)) return(list())
      x <- s$metrics[s$metrics$run_id == run & s$metrics$source == src, ]
      as.list(setNames(x$value, x$metric))
    }
    ms <- m_now("scholar", rs); mo <- m_now("openalex", ro)
    base <- if (unit == "scholar") ms else mo
    A <- base$academic_age %||% NA_real_
    first_year <- as.integer(format(snap, "%Y")) - A + 1
    cfg_fy <- meta$cfg_first_year[meta$author_id == aid]
    if (length(cfg_fy) && cfg_fy > 0) first_year <- cfg_fy
    pyc <- if (is.na(rs) || is.null(s$profile_year_counts)) NULL else
      tibble::as_tibble(s$profile_year_counts[s$profile_year_counts$run_id == rs, c("year", "citations")]) |>
      dplyr::mutate(author_id = aid)
    wyc <- s$work_year_counts[s$work_year_counts$source == "openalex" &
                                s$work_year_counts$work_id %in% p$work_id, c("work_id", "year", "citations")]
    fc <- forecast_author(p, wyc, snap, unit, nsim = nsim, seed = seed)
    list(aid = aid, rs = rs, ro = ro, snap = snap, unit = unit, papers = fc$papers, paper_fc = fc$paper_fc,
         author_fc = fc$author_fc, pyc = pyc, wyc = tibble::as_tibble(wyc) |> dplyr::mutate(author_id = aid),
         recon = reconstruct(s, aid, ro), ms = ms, mo = mo, first_year = first_year)
  })
  names(per) <- meta$author_id

  papers <- dplyr::bind_rows(lapply(per, `[[`, "papers"))
  cy_of <- function(d) as.integer(format(d, "%Y"))

  compare <- dplyr::bind_rows(lapply(per, function(x) {
    p <- x$papers
    c_unit <- if (x$unit == "scholar") p$scholar else p$openalex
    c_unit <- c_unit[!is.na(c_unit)]
    base <- if (x$unit == "scholar") x$ms else x$mo
    cy <- cy_of(x$snap)
    py <- x$pyc
    last_full <- if (!is.null(py)) sum(py$citations[py$year == cy - 1]) else
      sum(x$wyc$citations[x$wyc$year == cy - 1])
    prev_full <- if (!is.null(py)) sum(py$citations[py$year == cy - 2]) else
      sum(x$wyc$citations[x$wyc$year == cy - 2])
    roles <- table(factor(p$role, levels = c("Sole", "First", "Middle", "Last", "Unknown")))
    known <- sum(roles[c("Sole", "First", "Middle", "Last")])
    recent <- p$year >= cy - 5 & p$year <= cy - 1
    af <- x$author_fc
    h1 <- af[af$k == 1 & af$quantity == "h", ]
    c1 <- af[af$k == 1 & af$quantity == "citations_added", ]
    top10_sorted <- sort(c_unit, decreasing = TRUE)
    tibble::tibble(
      author_id = x$aid, unit = x$unit, snapshot_date = x$snap, first_year = x$first_year,
      academic_age = base$academic_age %||% NA, n_papers = length(c_unit), citations = sum(c_unit),
      h = h_of(c_unit), g = g_of(c_unit), i10 = sum(c_unit >= 10),
      m_quotient = h_of(c_unit) / (base$academic_age %||% NA), e = base$e %||% NA, hc = base$hc %||% NA,
      hI_norm = base$hI_norm %||% NA, hI_annual = base$hI_annual %||% NA, awcr = base$awcr %||% NA,
      awcr_pa = base$awcr_pa %||% NA,
      fwci_median = x$mo$fwci_median %||% NA, fwci_mean = x$mo$fwci_mean %||% NA,
      top10_share = x$mo$top10_share %||% NA, top1_share = x$mo$top1_share %||% NA,
      cites_per_paper = mean(c_unit), cites_per_year = sum(c_unit) / (base$academic_age %||% NA),
      cites_last_full_year = last_full, growth_yoy = if (prev_full > 0) last_full / prev_full - 1 else NA,
      papers_per_year_5y = sum(recent, na.rm = TRUE) / 5,
      gini = gini_of(c_unit), top10_paper_share = sum(utils::head(top10_sorted, 10)) / max(sum(c_unit), 1),
      share_first_or_sole = if (known) (roles[["First"]] + roles[["Sole"]]) / known else NA,
      share_last = if (known) roles[["Last"]] / known else NA,
      median_coauthors = stats::median(p$n_authors, na.rm = TRUE),
      velocity = x$ms$velocity_per_year %||% NA,
      proj_h_12m = h1$q50 %||% NA, proj_h_12m_lo = h1$q10 %||% NA, proj_h_12m_hi = h1$q90 %||% NA,
      proj_cites_12m = c1$q50 %||% NA, proj_cites_12m_lo = c1$q10 %||% NA, proj_cites_12m_hi = c1$q90 %||% NA
    )
  }))
  compare <- dplyr::left_join(compare, meta, by = "author_id")

  # Works shared between tracked authors (same OpenAlex work on both records).
  oa <- papers[!is.na(papers$openalex_id), c("author_id", "openalex_id")]
  shared <- dplyr::inner_join(oa, oa, by = "openalex_id", suffix = c("_a", "_b"), relationship = "many-to-many") |>
    dplyr::filter(author_id_a != author_id_b) |> dplyr::count(author_id_a, author_id_b, name = "n_shared")

  flags_latest <- if (is.null(s$flags) || nrow(s$flags) == 0) NULL else {
    last_run <- runs |> dplyr::filter(origin == "sync") |> dplyr::group_by(author_id) |>
      dplyr::summarise(run_id = max(run_id), .groups = "drop")
    s$flags |> dplyr::inner_join(last_run, by = "run_id") |>
      dplyr::left_join(s$works[, c("work_id", "gs_id", "openalex_id", "title")], by = "work_id")
  }

  list(
    built = Sys.time(), site_url = site_url, nsim = nsim,
    authors = meta |> dplyr::left_join(
      dplyr::bind_rows(lapply(per, function(x) tibble::tibble(author_id = x$aid, snapshot_date = x$snap,
                                                             unit = x$unit, first_year = x$first_year,
                                                             rs = x$rs, ro = x$ro))), by = "author_id"),
    compare = compare,
    metrics = tibble::as_tibble(metrics),
    papers = papers,
    paper_fc = dplyr::bind_rows(lapply(per, `[[`, "paper_fc")),
    author_fc = dplyr::bind_rows(lapply(per, `[[`, "author_fc")),
    paper_hist = tibble::as_tibble(s$work_snapshots) |>
      dplyr::inner_join(runs[, c("run_id", "author_id", "snapshot_date", "origin")], by = "run_id") |>
      dplyr::filter(work_id %in% papers$work_id),
    wyc = dplyr::bind_rows(lapply(per, `[[`, "wyc")),
    pyc = dplyr::bind_rows(lapply(per, `[[`, "pyc")),
    recon = dplyr::bind_rows(lapply(per, `[[`, "recon")),
    shared = shared,
    runs = tibble::as_tibble(runs),
    flags = flags_latest
  )
}

load_bundle <- function(app_dir = ".") {
  f <- Sys.getenv("PUBTRACK_BUNDLE", file.path(app_dir, "data", "bundle.rds"))
  if (file.exists(f)) return(readRDS(f))
  store <- Sys.getenv("PUBTRACK_DATA", file.path(app_dir, "..", "data"))
  yml <- file.path(store, "authors.yml")
  if (!file.exists(yml)) yml <- file.path(app_dir, "..", "authors.yml")
  build_bundle(store, yml, nsim = 1000)
}
