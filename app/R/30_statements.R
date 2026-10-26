# Copy ready impact statements for letters and emails. Plain declaratives, no em dashes, every
# number drawn from the bundle. Four to six sentences for an author, three to five for a paper.

.date_long <- function(d) paste(as.integer(format(d, "%d")), format(d, "%B %Y"))
.times <- function(x) paste0(formatC(x, format = "f", digits = 1), " times")
.cite_phrase <- function(r, unit_lab) {
  v <- if (!is.na(r$venue) && nzchar(r$venue)) paste0(", ", clean_venue(r$venue)) else ""
  paste0("“", r$title, "” (", if (is.na(r$year)) "n.d." else r$year, v, "; ", fmt_n(r$unit_citations), " citations)")
}
.join_and <- function(x) {
  if (length(x) <= 1) return(x)
  if (length(x) == 2) return(paste(x, collapse = " and "))
  paste0(paste(x[-length(x)], collapse = ", "), ", and ", x[length(x)])
}

top_papers <- function(b, aid, rank_by = c("citations", "fwci", "rate"), n = 3) {
  rank_by <- match.arg(rank_by)
  p <- b$papers[b$papers$author_id == aid & !is.na(b$papers$unit_citations), ]
  ok <- isTRUE(b$compare$forecast_ok[b$compare$author_id == aid])
  key <- switch(rank_by,
    citations = p$unit_citations,
    fwci = ifelse(p$unit_citations >= 10, p$fwci, NA),
    rate = if (ok) p$f12_med else rep(NA_real_, nrow(p)))  # ranking by forecast honours the gate
  p <- p[!is.na(key), ]; key <- key[!is.na(key)]
  p[order(-key, -p$unit_citations, na.last = TRUE), ][seq_len(min(n, nrow(p))), ]
}

impact_statement <- function(b, aid, rank_by = "citations", n_top = 3, forecast = TRUE, name = NULL) {
  cm <- b$compare[b$compare$author_id == aid, ]
  if (nrow(cm) == 0) return("")
  nm <- name %||% cm$name
  last <- sub("^.* ", "", nm)
  unit_lab <- if (cm$unit == "scholar") "Google Scholar" else "OpenAlex"
  s <- character()
  s[1] <- paste0("As of ", .date_long(cm$unit_date), ", ", nm, " has ", fmt_n(cm$n_papers),
                 " publications indexed in ", unit_lab, " with ", fmt_n(cm$citations), " citations, an h index of ",
                 cm$h, ", and an i10 index of ", cm$i10, ".")
  yr <- as.integer(format(cm$unit_date, "%Y")) - 1
  grow <- if (!is.na(cm$growth_yoy) && abs(cm$growth_yoy) >= 0.02)
    paste0(", ", if (cm$growth_yoy > 0) "up " else "down ", fmt_pct(abs(cm$growth_yoy)), " from the year before") else ""
  s[2] <- paste0("These publications drew ", fmt_n(cm$cites_last_full_year), " citations in ", yr, grow,
                 if (!is.na(cm$academic_age)) paste0(", and the m quotient over a ", round(cm$academic_age),
                                                      " year publishing career is ", formatC(cm$m_quotient, format = "f", digits = 2)) else "",
                 ".")
  if (!is.na(cm$fwci_median)) {
    s[length(s) + 1] <- paste0("By OpenAlex field normalized measures, the median paper is cited at ",
                               .times(cm$fwci_median), " the world average for its subfield, year, and document type",
                               if (!is.na(cm$top10_share)) paste0(", and ", fmt_pct(cm$top10_share), " of the ",
                                                                  fmt_n(cm$n_with_percentile),
                                                                  " papers with a field percentile rank in the top 10%") else "",
                               ".")
  }
  tp <- top_papers(b, aid, rank_by, n_top)
  if (nrow(tp)) {
    lead <- switch(rank_by, citations = "The most cited works include ", fwci = "The works with the highest field weighted impact include ",
                   rate = "The works gaining citations fastest include ")
    s[length(s) + 1] <- paste0(lead, .join_and(vapply(seq_len(nrow(tp)), function(i) .cite_phrase(tp[i, ], unit_lab), "")), ".")
  }
  if (!is.na(cm$share_first_or_sole) && !is.na(cm$share_last)) {
    s[length(s) + 1] <- paste0(last, " is first or sole author on ", fmt_pct(cm$share_first_or_sole),
                               " of papers with known author order and last author on ", fmt_pct(cm$share_last), ".")
  }
  if (forecast && !is.na(cm$proj_h_12m) && isTRUE(cm$forecast_ok)) {
    s[length(s) + 1] <- paste0("At current citation rates the h index is projected to reach ", round(cm$proj_h_12m),
                               " within twelve months (80% interval ", round(cm$proj_h_12m_lo), " to ",
                               round(cm$proj_h_12m_hi), "), with about ", fmt_n(cm$proj_cites_12m),
                               " new citations to existing papers.")
  }
  paste(utils::head(s, 6), collapse = " ")
}

paper_statement <- function(b, aid, wid) {
  p <- b$papers[b$papers$author_id == aid & b$papers$work_id == wid, ]
  if (nrow(p) == 0) return("")
  cm <- b$compare[b$compare$author_id == aid, ]
  all <- b$papers[b$papers$author_id == aid & !is.na(b$papers$unit_citations), ]
  rank <- sum(all$unit_citations > p$unit_citations) + 1
  unit_lab <- if (cm$unit == "scholar") "Google Scholar" else "OpenAlex"
  venue <- if (!is.na(p$venue) && nzchar(p$venue)) paste0(" in ", clean_venue(p$venue)) else ""
  s <- paste0("“", p$title, "”, published", venue, if (is.na(p$year)) " (n.d.)" else paste0(" in ", p$year), ", has been cited ",
              fmt_n(p$unit_citations), " times in ", unit_lab,
              if (cm$unit == "scholar" && !is.na(p$openalex)) paste0(" (", fmt_n(p$openalex), " in OpenAlex)") else "",
              ", ranking ", rank, " of ", nrow(all), " papers by ", cm$name, ".")
  if (!is.na(p$fwci)) {
    s <- c(s, paste0("Its field weighted citation impact is ", formatC(p$fwci, format = "f", digits = 2),
                     ", meaning it is cited at ", .times(p$fwci), " the world average for comparable papers",
                     if (p$top1 %in% 1) ", placing it in the top 1% of its field" else if (p$top10 %in% 1)
                       ", placing it in the top 10% of its field" else "", "."))
  }
  yr <- as.integer(format(cm$openalex_date %||% cm$snapshot_date, "%Y")) - 1
  last_full <- sum(b$wyc$citations[b$wyc$author_id == aid & b$wyc$work_id == wid & b$wyc$year == yr])
  if (!is.na(p$velocity)) {
    s <- c(s, paste0("Over the past year it gained about ", fmt_n(p$velocity), " citations on Google Scholar."))
  } else if (last_full > 0) {
    s <- c(s, paste0("It received ", fmt_n(last_full), " OpenAlex citations in ", yr, "."))
  }
  if (!is.na(p$f12_med) && isTRUE(cm$forecast_ok)) {
    s <- c(s, paste0("Its current trajectory projects about ", fmt_n(p$f12_med), " further citations in the next twelve months (80% interval ",
                     fmt_n(p$f12_lo), " to ", fmt_n(p$f12_hi), ")."))
  }
  paste(s, collapse = " ")
}


# One formatter for "median (lo to hi)" cells; "n/a" when the forecast is absent or gated off.
fmt_interval <- function(med, lo, hi, gated = TRUE, digits = 0) {
  ifelse(is.na(med) | !gated, "n/a", paste0(fmt_n(med, digits), " (", fmt_n(lo, digits), " to ", fmt_n(hi, digits), ")"))
}

# Key indices table shared by the in app report and the static report. Window rows are OpenAlex
# for every author; the unit rows are in the author's unit, named in the first row.
key_indices_table <- function(x) {
  tibble::tibble(
    Metric = c(paste0("Papers (", .unit_lab(x$unit), ")"), "Citations", "h index", "g index", "i10 index", "m quotient",
               "Contemporary h", "hI norm (papers with known author count)",
               "Papers per career year", "Citations per career year", "Citations per paper per year (median)",
               "Citations per year, last 5 complete years (OpenAlex)", "h5, last 5 complete years (OpenAlex)",
               "Papers published, last 5 complete years (OpenAlex)", "h of papers from last 5 complete years (OpenAlex)",
               "Median FWCI (OpenAlex)", paste0("Share in top 10% (OpenAlex, of ", fmt_n(x$n_with_percentile), " papers with a percentile)"),
               paste("Citations in", as.integer(format(x$unit_date, "%Y")) - 1), "Growth on previous year",
               "Projected h in 12 months (80% interval)", "Projected new citations in 12 months (80% interval)"),
    Value = c(fmt_n(x$n_papers), fmt_n(x$citations), fmt_n(x$h), fmt_n(x$g), fmt_n(x$i10), fmt_n(x$m_quotient, 2),
              fmt_n(x$hc), fmt_n(x$hI_norm),
              fmt_n(x$papers_per_year, 1), fmt_n(x$cites_per_year), fmt_n(x$cites_per_paper_year_median, 2),
              fmt_n(x$cites_per_year_5y), fmt_n(x$h5), fmt_n(x$papers_5y), fmt_n(x$h_recent),
              fmt_n(x$fwci_median, 2), fmt_pct(x$top10_share),
              fmt_n(x$cites_last_full_year), fmt_pct(x$growth_yoy),
              fmt_interval(x$proj_h_12m, x$proj_h_12m_lo, x$proj_h_12m_hi, isTRUE(x$forecast_ok)),
              fmt_interval(x$proj_cites_12m, x$proj_cites_12m_lo, x$proj_cites_12m_hi, isTRUE(x$forecast_ok))))
}
