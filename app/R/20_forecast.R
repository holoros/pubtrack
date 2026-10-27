# Citation forecasts with honest uncertainty.
#
# Each paper gets an annual citation rate from its OpenAlex yearly counts:
#   trend    at least four complete years and five citations: Poisson log linear fit over the last
#            six complete years, log mu = a + b t (t centred). Slopes are shrunk toward the author's
#            slope distribution by empirical Bayes (method of moments), so a short noisy run cannot
#            dominate; growth past the last complete year is damped (factor 0.8 a year) and bounded
#            to [-0.5, 0.4]. Parameter uncertainty enters through draws of level and slope; count
#            noise is negative binomial with variance phi * mu (Pearson dispersion).
#   rate     fewer years: a constant rate with a Gamma posterior (Jeffreys prior) on the latest
#            complete year plus the year to date, since young papers are still ramping up.
#   lifetime no OpenAlex match: Scholar citations over paper age, Gamma(c + 0.5, age); or the weekly
#            Scholar velocity when eight or more weekly points exist.
# OpenAlex rates convert to Scholar units with the paper's own Scholar to OpenAlex ratio shrunk toward
# the author's ratio (ten pseudo citations). The forecast covers papers already on the record: new
# papers and citations to them are excluded, so author totals are conservative.

FC_SLOPE <- c(-0.5, 0.4)
FC_DAMP <- 0.8   # damped trend: growth beyond the last complete year decays by 20% a year
FC_MIN_EXPOSURE <- 0.25  # years; floor on exposure for papers dated the current year


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
  fits <- vector("list", np)
  prep <- vector("list", np)
  # Pass 1: data and per paper fits. Time is centred on the fitted years so level and slope are
  # nearly independent; t0 is the centred position of the last complete year.
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
    prep[[i]] <- list(pub = pub, has_oa = has_oa, k = k_i, yrs = yrs, y = y, partial = partial)
    if (has_oa && length(yrs) >= 4 && sum(y) >= 5) {
      tc <- yrs - mean(yrs)
      fit <- tryCatch(suppressWarnings(stats::glm(y ~ tc, family = stats::poisson())), error = function(e) NULL)
      if (!is.null(fit) && fit$converged) {
        phi <- min(max(sum(stats::residuals(fit, "pearson")^2) / max(length(y) - 2, 1), 1), 20)
        V <- stats::vcov(fit) * phi
        fits[[i]] <- list(a = unname(stats::coef(fit)[1]), b = unname(stats::coef(fit)[2]), V = V, phi = phi,
                          t0 = (cy - 1) - mean(yrs))
      }
    }
  }
  # Empirical Bayes shrinkage of slopes toward the author's slope distribution (method of moments).
  ok <- which(!vapply(fits, is.null, logical(1)))
  if (length(ok)) {
    bh <- vapply(fits[ok], `[[`, numeric(1), "b")
    se2 <- vapply(fits[ok], function(z) z$V[2, 2], numeric(1))
    w0 <- 1 / se2
    b_pop <- if (length(ok) >= 5) sum(w0 * bh) / sum(w0) else 0
    tau2 <- if (length(ok) >= 5) max(stats::var(bh) - mean(se2), 0.0025) else 0.01
    for (j in seq_along(ok)) {
      v_post <- 1 / (1 / se2[j] + 1 / tau2)
      fits[[ok[j]]]$b_post <- v_post * (bh[j] / se2[j] + b_pop / tau2)
      fits[[ok[j]]]$b_sd <- sqrt(v_post)
    }
  }
  damp <- function(h) (1 - FC_DAMP^h) / (1 - FC_DAMP)
  # Pass 2: simulate.
  for (i in seq_len(np)) {
    d <- prep[[i]]; fz <- fits[[i]]
    method <- "lifetime"; a <- NA_real_; b <- NA_real_; phi <- 1
    if (!is.null(fz)) {
      method <- "trend"; a <- fz$a; b <- fz$b_post; phi <- fz$phi
      ad <- stats::rnorm(nsim, fz$a, sqrt(fz$V[1, 1]))
      bd <- pmin(pmax(stats::rnorm(nsim, fz$b_post, fz$b_sd), FC_SLOPE[1]), FC_SLOPE[2])
      for (k in seq_len(H)) {
        m <- d$k * exp(ad + bd * fz$t0 + bd * damp(k + f))
        draws[, i, k] <- if (phi > 1.05) stats::rnbinom(nsim, mu = m, size = m / (phi - 1)) else stats::rpois(nsim, m)
      }
    } else if (d$has_oa) {
      method <- "rate"
      # Young papers are still ramping up, so the rate rests on the latest complete year plus the
      # year to date rather than the whole record.
      last <- utils::tail(d$y, 1)
      # A paper dated this year has no complete year: its exposure is the year to date, floored at a
      # quarter so a January snapshot cannot turn 0 or 1 citations into a rate of tens per year.
      expo <- if (length(last)) length(last) + f else max(f, FC_MIN_EXPOSURE)
      lam <- stats::rgamma(nsim, shape = sum(last) + d$partial + 0.5, rate = expo)
      for (k in seq_len(H)) draws[, i, k] <- stats::rpois(nsim, d$k * lam)
    } else {
      v <- p$velocity[i]
      lam <- if (!is.na(v) && v > 0) stats::rgamma(nsim, shape = 11, rate = 11 / v) else {
        age <- if (is.na(d$pub)) 5 else max(cy + f - d$pub, 0.5)
        stats::rgamma(nsim, shape = c_unit[i] + 0.5, rate = age)
      }
      for (k in seq_len(H)) draws[, i, k] <- stats::rpois(nsim, lam)
    }
    info[[i]] <- tibble::tibble(work_id = p$work_id[i], fc_method = method, fit_a = a, fit_b = b, fit_phi = phi,
                                fit_t0 = if (is.null(fz)) NA_real_ else fz$t0, k_scale = d$k)
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
      trend_pct = ifelse(fc_method == "trend", exp(fit_b) - 1, NA_real_)
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
  d <- papers_unit |> dplyr::arrange(dplyr::desc(unit_citations)) |>
    dplyr::mutate(rank = dplyr::row_number(), needed = pmax(h + 1 - unit_citations, 0)) |>
    dplyr::filter(needed > 0)
  if (nrow(d) == 0) return(d)
  # every paper as close as the (h + 1)th, not only those ranked at or above it
  thr <- min(d$needed[d$rank <= h + 1], max(d$needed))
  dplyr::filter(d, needed <= thr)
}

# Backtest: refit with OpenAlex counts through the year before last, forecast last year, and compare
# with what OpenAlex recorded. Units are OpenAlex throughout, so no scaling enters the check.
# Returns the total (predicted interval against observed) and per paper 80% interval coverage.
backtest_author <- function(p, wyc, snap, nsim = 1000, seed = 7) {
  cy <- as.integer(format(snap, "%Y"))
  target <- cy - 1
  after <- wyc |> dplyr::filter(year >= target) |> dplyr::group_by(work_id) |>
    dplyr::summarise(after = sum(citations), .groups = "drop")
  pb <- p |> dplyr::filter(!is.na(openalex), !is.na(openalex_id), !is.na(year), year <= target - 1) |>
    dplyr::left_join(after, by = "work_id") |>
    dplyr::mutate(openalex = pmax(openalex - dplyr::coalesce(after, 0L), 0), scholar = NA_real_, velocity = NA_real_) |>
    dplyr::select(-after)
  if (nrow(pb) < 10) return(NULL)
  fc <- forecast_author(pb, wyc[wyc$year <= target - 1, ], as.Date(sprintf("%d-01-01", target)),
                        unit = "openalex", nsim = nsim, seed = seed, H = 1)
  obs <- wyc |> dplyr::filter(year == target) |> dplyr::group_by(work_id) |>
    dplyr::summarise(obs = sum(citations), .groups = "drop")
  pf <- fc$paper_fc |> dplyr::filter(k == 1) |> dplyr::left_join(obs, by = "work_id") |>
    dplyr::mutate(obs = dplyr::coalesce(obs, 0L))
  tot <- fc$author_fc |> dplyr::filter(k == 1, quantity == "citations_added")
  tibble::tibble(
    target_year = target, n_papers = nrow(pf), observed = sum(pf$obs),
    pred_q10 = tot$q10, pred_q50 = tot$q50, pred_q90 = tot$q90, pred_mean = tot$mean,
    ratio = tot$q50 / max(sum(pf$obs), 1),
    total_in_80 = sum(pf$obs) >= tot$q10 & sum(pf$obs) <= tot$q90,
    paper_cov80 = mean(pf$obs >= pf$q10 & pf$obs <= pf$q90),
    paper_mae = mean(abs(pf$obs - pf$q50)),
    by_method = list(fc$papers |> dplyr::select(work_id, fc_method) |> dplyr::right_join(pf, by = "work_id") |>
                       dplyr::group_by(fc_method) |>
                       dplyr::summarise(n = dplyr::n(), cov80 = mean(obs >= q10 & obs <= q90), pred = sum(q50),
                                        observed = sum(obs), .groups = "drop"))
  )
}


# Young paper check: the main backtest cannot score papers published in the target year, since it
# forecasts from 1 January. This one uses the year to date counts the collector has stored per run
# (work_ytd, since 0.2.4): find a run about a year before the snapshot, take the papers published in
# that run's year, reproduce the rate the forecast would have drawn then ((ytd + 0.5) / exposure,
# exposure floored as in forecast_author), and compare the implied next twelve months with what
# OpenAlex recorded: the rest of that year plus the matching fraction of the following year.
# Returns NULL until such a run exists (first possible: October 2027).
backtest_young <- function(p, wyc, ytd, runs_a, ro, snap, tol_days = 14) {
  if (is.null(ytd) || nrow(ytd) == 0 || is.na(ro)) return(NULL)
  then <- runs_a[runs_a$openalex_status %in% "ok" & abs(as.numeric(runs_a$snapshot_date - (snap - 365))) <= tol_days, ]
  if (nrow(then) == 0) return(NULL)
  r0 <- then$run_id[which.min(abs(as.numeric(then$snapshot_date - (snap - 365))))]
  d0 <- then$snapshot_date[then$run_id == r0]
  Y <- as.integer(format(d0, "%Y"))
  if (Y >= as.integer(format(snap, "%Y"))) return(NULL)   # need the following year to be observable
  f0 <- (as.integer(format(d0, "%j")) - 0.5) / 365.25
  y0 <- ytd[ytd$run_id == r0 & ytd$year == Y, c("work_id", "citations")]
  pub <- p[!is.na(p$openalex_id) & dplyr::coalesce(p$year_openalex, p$year) %in% Y, c("work_id", "title")]
  d <- dplyr::inner_join(pub, y0, by = "work_id")
  if (nrow(d) < 5) return(NULL)
  full <- wyc |> dplyr::filter(year %in% c(Y, Y + 1)) |> tidyr::pivot_wider(id_cols = work_id, names_from = year,
                                                                          values_from = citations, values_fill = 0)
  d <- d |> dplyr::left_join(full, by = "work_id")
  cY <- d[[as.character(Y)]] %||% rep(0, nrow(d)); cY1 <- d[[as.character(Y + 1)]] %||% rep(0, nrow(d))
  d$predicted <- (d$citations + 0.5) / max(f0, FC_MIN_EXPOSURE)
  d$observed <- pmax(cY - d$citations, 0) + f0 * cY1
  tibble::tibble(ytd_run = r0, ytd_date = d0, year = Y, n_papers = nrow(d), predicted = sum(d$predicted),
                 observed = sum(d$observed), ratio = sum(d$predicted) / max(sum(d$observed), 1))
}
