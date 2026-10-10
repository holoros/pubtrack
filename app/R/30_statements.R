# Copy ready impact statements for letters and emails. Plain declaratives, no em dashes, every
# number drawn from the bundle. Four to six sentences for an author, three to five for a paper.

.date_long <- function(d) paste(as.integer(format(d, "%d")), format(d, "%B %Y"))
.times <- function(x) paste0(formatC(x, format = "f", digits = 1), " times")
.cite_phrase <- function(r, unit_lab) {
  v <- if (!is.na(r$venue) && nzchar(r$venue)) paste0(", ", sub(",.*$", "", r$venue)) else ""
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
  key <- switch(rank_by,
    citations = p$unit_citations,
    fwci = ifelse(p$unit_citations >= 10, p$fwci, NA),
    rate = p$f12_med)
  p[order(-key, -p$unit_citations, na.last = TRUE), ][seq_len(min(n, nrow(p))), ]
}

impact_statement <- function(b, aid, rank_by = "citations", n_top = 3, forecast = TRUE, name = NULL) {
  cm <- b$compare[b$compare$author_id == aid, ]
  if (nrow(cm) == 0) return("")
  nm <- name %||% cm$name
  last <- sub("^.* ", "", nm)
  unit_lab <- if (cm$unit == "scholar") "Google Scholar" else "OpenAlex"
  s <- character()
  s[1] <- paste0("As of ", .date_long(cm$snapshot_date), ", ", nm, " has ", fmt_n(cm$n_papers),
                 " publications indexed in ", unit_lab, " with ", fmt_n(cm$citations), " citations, an h index of ",
                 cm$h, ", and an i10 index of ", cm$i10, ".")
  yr <- as.integer(format(cm$snapshot_date, "%Y")) - 1
  grow <- if (!is.na(cm$growth_yoy) && abs(cm$growth_yoy) >= 0.02)
    paste0(", ", if (cm$growth_yoy > 0) "up " else "down ", fmt_pct(abs(cm$growth_yoy)), " from the year before") else ""
  s[2] <- paste0("These publications drew ", fmt_n(cm$cites_last_full_year), " citations in ", yr, grow,
                 if (!is.na(cm$academic_age)) paste0(", and the m quotient over a ", round(cm$academic_age),
                                                      " year publishing career is ", formatC(cm$m_quotient, format = "f", digits = 2)) else "",
                 ".")
  if (!is.na(cm$fwci_median)) {
    s[length(s) + 1] <- paste0("By OpenAlex field normalized measures, the median paper is cited at ",
                               .times(cm$fwci_median), " the world average for its subfield, year, and document type",
                               if (!is.na(cm$top10_share)) paste0(", and ", fmt_pct(cm$top10_share),
                                                                  " of papers rank in the top 10% of their field") else "",
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
  if (forecast && !is.na(cm$proj_h_12m)) {
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
  venue <- if (!is.na(p$venue) && nzchar(p$venue)) paste0(" in ", sub(",.*$", "", p$venue)) else ""
  s <- paste0("“", p$title, "”, published", venue, " in ", p$year, ", has been cited ",
              fmt_n(p$unit_citations), " times in ", unit_lab,
              if (cm$unit == "scholar" && !is.na(p$openalex)) paste0(" (", fmt_n(p$openalex), " in OpenAlex)") else "",
              ", ranking ", rank, " of ", nrow(all), " papers by ", cm$name, ".")
  if (!is.na(p$fwci)) {
    s <- c(s, paste0("Its field weighted citation impact is ", formatC(p$fwci, format = "f", digits = 2),
                     ", meaning it is cited at ", .times(p$fwci), " the world average for comparable papers",
                     if (p$top1 %in% 1) ", placing it in the top 1% of its field" else if (p$top10 %in% 1)
                       ", placing it in the top 10% of its field" else "", "."))
  }
  yr <- as.integer(format(cm$snapshot_date, "%Y")) - 1
  last_full <- sum(b$wyc$citations[b$wyc$author_id == aid & b$wyc$work_id == wid & b$wyc$year == yr])
  if (!is.na(p$velocity)) {
    s <- c(s, paste0("Over the past year it gained about ", fmt_n(p$velocity), " citations on Google Scholar."))
  } else if (last_full > 0) {
    s <- c(s, paste0("It received ", fmt_n(last_full), " OpenAlex citations in ", yr, "."))
  }
  if (!is.na(p$f12_med)) {
    s <- c(s, paste0("Its current trajectory projects about ", fmt_n(p$f12_med), " further citations in the next twelve months (80% interval ",
                     fmt_n(p$f12_lo), " to ", fmt_n(p$f12_hi), ")."))
  }
  paste(s, collapse = " ")
}
