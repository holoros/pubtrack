# Build data/bundle.rds for the app from the CSV store.
#   Rscript app/build_bundle.R [store_dir] [authors.yml] [out.rds]
# Defaults: ../data, ../authors.yml, data/bundle.rds relative to this script's folder.
args <- commandArgs(trailingOnly = TRUE)
here <- dirname(normalizePath(sub("--file=", "", grep("--file=", commandArgs(), value = TRUE))))
store <- if (length(args) >= 1) args[[1]] else file.path(here, "..", "data")
yml <- if (length(args) >= 2) args[[2]] else file.path(here, "..", "authors.yml")
out <- if (length(args) >= 3) args[[3]] else file.path(here, "data", "bundle.rds")
suppressPackageStartupMessages({ library(dplyr); library(tidyr); library(ggplot2) })
for (f in sort(list.files(file.path(here, "R"), pattern = "\\.R$", full.names = TRUE))) source(f)
site <- Sys.getenv("PUBTRACK_SITE_URL", "")
t0 <- Sys.time()
b <- build_bundle(store, yml, nsim = as.integer(Sys.getenv("PUBTRACK_NSIM", "2000")), site_url = site)
dir.create(dirname(out), recursive = TRUE, showWarnings = FALSE)
saveRDS(b, out, compress = "xz")
cat(sprintf("bundle: %s  authors=%d papers=%d metrics=%d  %.1f s  %.0f kB\n", out, nrow(b$authors), nrow(b$papers),
            nrow(b$metrics), as.numeric(difftime(Sys.time(), t0, units = "secs")), file.size(out) / 1024))
print(as.data.frame(b$compare[, c("author_id", "unit", "n_papers", "citations", "h", "proj_h_12m", "proj_h_12m_lo", "proj_h_12m_hi")]))
