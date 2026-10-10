# Citation forecasts with honest uncertainty.
#
# Each paper gets an annual citation rate from its OpenAlex yearly counts:
#   trend    at least four complete years and five citations: Poisson log linear fit over the last
#            six complete years, log mu = a + b t. Parameter uncertainty enters through draws of (a, b)
#            from their estimated covariance inflated by the Pearson dispersion phi; count noise is
#            negative binomial with variance phi * mu. The slope is bounded to [-0.5, 0.4] per year
#            so a short run of years cannot extrapolate explosively.
#   rate     fewer years: a constant rate with a Gamma(sum + 0.5, years) posterior (Jeffreys prior),
#            using the current partial year as exposure.
#   lifetime no OpenAlex match: Scholar citations over paper age, Gamma(c + 0.5, age); or the weekly
#            Scholar velocity when eight or more weekly points exist.
# OpenAlex rates convert to Scholar units with the paper's own Scholar to OpenAlex ratio shrunk toward
# the author's ratio (ten pseudo citations). The forecast covers papers already on the record: new
# papers and citations to them are excluded, so author totals are conservative.

FC_SLOPE <- c(-0.5, 0.4)

.rmvn2 <- function(n, mu, V) {
  L <- tryCatch(chol(V), error = function(e) NULL)
  if (is.null(L)) L <- diag(sqrt(pmax(diag(V), 0)))
  z <- matrix(stats::rnorm(n * 2), n, 2)
  sweep(z %*% L, 2, mu, "+")
}

.qtab <- function(x, probs = c(0.025, 0.1, 0.5, 0.9, 0.975)) {
  q <- stats::quantile(x, probs, names = FALSE, type = 7)
  tibble::tibble(q025 = q[1], q10 = q[2], q50 = q[3], q90 = q[4], q975 = q[5], mean = mean(x))
}

forecast_author <- function(p, wyc, snap, unit = "scholar", nsim = 2000, seed = 1, H = 3) {
  set.seed(seed)
  cy <- as.integer(format(snap, "%Y"))
  f <- (as.integer(format(snap, "%j")) - 0.5) / 365.25
  c_unit <- if (unit == "scholar") p$scholar else p$openalex
  keep <- !is.na(c_unit)
  p <- p[keep, ]
  c_unit <- c_unit[keep]
  np <- nrow(p)
  empty <- list(papers = p, paper_fc = NULL, author_fc = NULL)
  if (np == 0) return(empty)

  both <- !is.na(p$scholar) & !is.na(p$openalex) & p$openalex > 0
  K <- if (unit == "scholar" && any(both)) sum(p$scholar[both]) / sum(p$openalex[both]) else 1
  K <- min(max(K, 0.5), 5)

  draws <- array(0L, dim = c(nsim, np, H))
  info <- vector("list", np)
  for (i in seq_len(np)) {
    pub <- p$year[i]
    y_all <- wyc[wyc$work_id == p$work_id[i], c("year", "citations")]
    has_oa <- !is.na(p$openalex_id[i]) && !is.na(p$openalex[i])
    k_i <- if (unit == "scholar" && has_oa) (p$scholar[i] + 10 * K) / (p$openalex[i] + 10) else 1
    first <- max(if (is.na(pub)) cy - 6 else pub, cy - 6)
    yrs <- if (first <= cy - 1) seq(first, cy - 1) else integer()
    y <- y_all$citations[match(yrs, y_all$year)]
    y[is.na(y)] <- 0
    partial <- sum(y_all$citations[y_all$year == cy])
    method <- "lifetime"; a <- NA_real_; b <- NA_real_; phi <- 1
    if (has_oa && length(yrs) >= 4 && sum(y) >= 5) {
      t <- yrs - (cy - 1)
      fit <- tryCatch(suppressWarnings(stats::glm(y ~ t, family = stats::poisson())), error = function(e) NULL)
      if (!is.null(fit) && fit$converged) {
        method <- "trend"
        a <- unname(stats::coef(fit)[1]); b <- unname(stats::coef(fit)[2])
        phi <- min(max(sum(stats::residuals(fit, "pearson")^2) / max(length(y) - 2, 1), 1), 20)
        ab <- .rmvn2(nsim, c(a, b), stats::vcov(fit) * phi)
        ab[, 2] <- pmin(pmax(ab[, 2], FC_SLOPE[1]), FC_SLOPE[2])
        for (k in seq_len(H)) {
          m <- k_i * exp(ab[, 1] + ab[, 2] * (k + f))
          draws[, i, k] <- if (phi > 1.05) stats::rnbinom(nsim, mu = m, size = m / (phi - 1)) else stats::rpois(nsim, m)
        }
      }
    }
    if (method == "lifetime" && has_oa) {
      method <- "rate"
      lam <- stats::rgamma(nsim, shape = sum(y) + partial + 0.5, rate = length(yrs) + f)
      for (k in seq_len(H)) draws[, i, k] <- stats::rpois(nsim, k_i * lam)
    }
    if (method == "lifetime") {
      v <- p$velocity[i]
      lam <- if (!is.na(v) && v > 0) stats::rgamma(nsim, shape = 11, rate = 11 / v) else {
        age <- if (is.na(pub)) 5 else max(cy + f - pub, 0.5)
        stats::rgamma(nsim, shape = c_unit[i] + 0.5, rate = age)
      }
      for (k in seq_len(H)) draws[, i, k] <- stats::rpois(nsim, lam)
    }
    info[[i]] <- tibble::tibble(work_id = p$work_id[i], fc_method = method, fit_a = a, fit_b = b, fit_phi = phi,
                                k_scale = k_i)
  }

  info <- dplyr::bind_rows(info)
  next12 <- draws[, , 1, drop = FALSE][, , 1]
  if (np == 1) next12 <- matrix(next12, ncol = 1)
  p <- p |> dplyr::left_join(info, by = "work_id") |>
    dplyr::mutate(
      unit_citations = c_unit,
      f12_med = apply(next12, 2, stats::median),
      f12_lo = apply(next12, 2, stats::quantile, 0.1),
      f12_hi = apply(next12, 2, stats::quantile, 0.9),
      trend_pct = ifelse(fc_method == "trend", exp(pmin(pmax(fit_b, FC_SLOPE[1]), FC_SLOPE[2])) - 1, NA_real_)
    )

  paper_fc <- dplyr::bind_rows(lapply(seq_len(H), function(k) {
    m <- draws[, , k, drop = FALSE][, , 1]
    if (np == 1) m <- matrix(m, ncol = 1)
    q <- apply(m, 2, stats::quantile, c(0.025, 0.1, 0.5, 0.9, 0.975))
    tibble::tibble(author_id = p$author_id[1], work_id = p$work_id, k = k,
                   year_mid = as.numeric(format(snap, "%Y")) + f + k - 1,
                   q025 = q[1, ], q10 = q[2, ], q50 = q[3, ], q90 = q[4, ], q975 = q[5, ])
  }))

  cum <- matrix(0, nsim, np)
  base_total <- sum(c_unit)
  author_fc <- dplyr::bind_rows(lapply(seq_len(H), function(k) {
    m <- draws[, , k, drop = FALSE][, , 1]
    if (np == 1) m <- matrix(m, ncol = 1)
    cum <<- cum + m
    tot <- sweep(cum, 2, c_unit, "+")
    hs <- apply(tot, 1, h_of)
    added <- rowSums(cum)
    dplyr::bind_rows(
      dplyr::mutate(.qtab(hs), quantity = "h"),
      dplyr::mutate(.qtab(added), quantity = "citations_added"),
      dplyr::mutate(.qtab(base_total + added), quantity = "citations_total"),
      dplyr::mutate(.qtab(apply(tot >= 10, 1, sum)), quantity = "i10")
    ) |> dplyr::mutate(author_id = p$author_id[1], k = k, date = snap + round(365.25 * k), unit = unit)
  }))
  now <- tibble::tibble(author_id = p$author_id[1], k = 0, date = snap, unit = unit,
                        quantity = c("h", "citations_added", "citations_total", "i10"),
                        q025 = c(h_of(c_unit), 0, base_total, sum(c_unit >= 10)))
  now <- dplyr::mutate(now, q10 = q025, q50 = q025, q90 = q025, q975 = q025, mean = q025)
  list(papers = p, paper_fc = paper_fc, author_fc = dplyr::bind_rows(now, author_fc))
}

# Papers closest to lifting h by one: the h+1 candidates and the citations each still needs.
h_frontier <- function(papers_unit) {
  c <- sort(papers_unit$unit_citations, decreasing = TRUE)
  h <- h_of(c)
  papers_unit |> dplyr::arrange(dplyr::desc(unit_citations)) |>
    dplyr::mutate(rank = dplyr::row_number(), needed = pmax(h + 1 - unit_citations, 0)) |>
    dplyr::filter(rank <= h + 1, rank >= max(h - 3, 1) | needed > 0) |>
    dplyr::filter(needed > 0)
}
