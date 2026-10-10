# Render one self contained HTML impact report per author from the bundle.
#   Rscript reports/render_all.R app/data/bundle.rds out_dir
invisible(Sys.setlocale("LC_CTYPE", "C.UTF-8"))
args <- commandArgs(trailingOnly = TRUE)
here <- dirname(normalizePath(sub("--file=", "", grep("--file=", commandArgs(), value = TRUE))))
bundle <- normalizePath(args[[1]]); out <- args[[2]]
dir.create(out, recursive = TRUE, showWarnings = FALSE); out <- normalizePath(out)
b <- readRDS(bundle)
for (aid in b$authors$author_id) {
  rmarkdown::render(file.path(here, "author_report.Rmd"), output_file = paste0(aid, ".html"), output_dir = out,
                    params = list(bundle = bundle, author = aid, app_r = normalizePath(file.path(here, "..", "app", "R"))),
                    intermediates_dir = tempdir(), envir = new.env(), quiet = TRUE)
  cat("rendered", aid, "\n")
}
idx <- paste0("<!doctype html><meta charset='utf-8'><title>pubtrack reports</title>",
              "<body style='font-family:sans-serif;max-width:720px;margin:40px auto;color:#14201a'><h1>Impact reports</h1><ul>",
              paste0("<li><a href='", b$authors$author_id, ".html'>", b$authors$name, "</a></li>", collapse = ""),
              "</ul><p><a href='../'>Dashboard</a></p></body>")
writeLines(idx, file.path(out, "index.html"))
