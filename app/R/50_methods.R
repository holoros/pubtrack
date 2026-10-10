methods_html <- function() {
  paste0(
    "<p><b>Sources.</b> Google Scholar profile pages (through SerpAPI or a local scrape) give per paper citation counts, ",
    "the profile's citations per year, and Scholar's own h and i10. OpenAlex gives DOIs, author lists and order, ",
    "citations per year for the last ten years, field weighted citation impact (FWCI), and citation percentiles. ",
    "Scholar and OpenAlex counts are never mixed inside one metric; each chart names its source.</p>",
    "<p><b>Matching.</b> Scholar entries pair with OpenAlex works by DOI, then exact normalized title, then fuzzy title ",
    "(token sort ratio of at least 92 that beats the runner up by 3), one to one, within a year of each other. Anything left ",
    "over is listed under Data, never guessed. Manual overrides (merge, exclude, set DOI, set OpenAlex id) are recorded once ",
    "and reapplied on every run.</p>",
    "<p><b>Indices.</b> h, g, i10, e, contemporary h (Sidiropoulos et al. 2007, gamma 4, delta 1), hI norm (citations divided ",
    "by author count), annual h, AWCR and AW, m quotient (h over academic age), and citation velocity (least squares slope ",
    "of weekly cumulative citations over the past year, shown once eight weekly points exist). Year end values before tracking ",
    "began are reconstructed from OpenAlex per work yearly counts and drawn dashed.</p>",
    "<p><b>Forecasts.</b> Each paper's annual citation rate comes from a Poisson log linear fit to its last six complete years ",
    "of OpenAlex counts (at least four years and five citations), with parameter uncertainty from the estimated covariance inflated ",
    "by the Pearson dispersion and negative binomial count noise; the yearly slope is bounded to between minus 39% and plus 49% a year. Younger ",
    "or sparser papers use a Gamma posterior on a constant rate; unmatched papers use their Scholar lifetime rate or weekly velocity. ",
    "Rates convert to Scholar units with each paper's Scholar to OpenAlex ratio shrunk toward the author's. Two thousand simulations ",
    "give 80% and 95% intervals for h, i10 and citations one to three years ahead. Only papers already on the record are projected, ",
    "so totals are conservative for active authors.</p>",
    "<p><b>Comparisons.</b> Academic age counts from the first publication year (or the year set in authors.yml). Heatmap shading ranks ",
    "each metric within the selected set only. Concentration curves and the Gini coefficient describe how citations spread across papers.</p>"
  )
}
