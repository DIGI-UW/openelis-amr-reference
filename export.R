# Translate the declared human reference scope; never guess a clinical context.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 6L) stop("Expected: library, organisms, guidelines, version, output, organism list")
.libPaths(c(args[1], .libPaths()))
suppressPackageStartupMessages(library(AMR))
Sys.setlocale("LC_COLLATE", "C")
options(scipen = 999, digits = 15)
organism_codes <- sort(unique(readLines(args[2])))
guidelines <- sort(unique(readLines(args[3])))
version <- args[4]
out <- args[5]
organism_list <- args[6]
if (!organism_list %in% c("rule-scope", "accepted-taxa")) stop("Unknown organism list")
mo <- as.data.frame(microorganisms, stringsAsFactors = FALSE)
ab <- as.data.frame(antimicrobials, stringsAsFactors = FALSE)
bp <- as.data.frame(clinical_breakpoints, stringsAsFactors = FALSE)
bp$source_row <- seq_len(nrow(bp))
bp <- bp[bp$type == "human" & bp$guideline %in% guidelines, , drop = FALSE]
if (!all(guidelines %in% bp$guideline)) stop("Unknown or empty guideline")
if (identical(organism_codes, "human-breakpoint-taxa")) {
  # Include source taxa and common accepted species beneath those taxa. Taxonomy
  # and membership come from this same pinned AMR revision, never name guessing.
  targets <- unique(as.character(bp$mo))
  names <- mo$fullname[match(targets, as.character(mo$mo))]
  members <- mo$genus %in% names | mo$family %in% names |
    mo$order %in% names | mo$class %in% names |
    as.character(mo$mo) %in% as.character(microorganisms.groups$mo[
      as.character(microorganisms.groups$mo_group) %in% targets
    ])
  organism_codes <- sort(unique(c(targets, as.character(mo$mo[
    members & mo$status == "accepted" & mo$rank == "species" & mo$prevalence == 1 &
      as.character(mo$mo) %in% as.character(microorganisms.codes$mo[
        grepl("^WHONET ", microorganisms.codes$source)
      ])
  ]))))
}
if (!all(organism_codes %in% as.character(mo$mo))) stop("Unknown organism code")
empty <- function(x) is.na(x) | x == ""
write_csv <- function(x, name) {
  write.table(
    x, file.path(out, name), sep = ",", row.names = FALSE,
    col.names = TRUE, na = "", qmethod = "double", fileEncoding = "UTF-8"
  )
}
message("Selected ", length(organism_codes), " organism concepts from ", nrow(bp), " source rows")
rows <- list()
vectors <- list()
coverage <- list()
used_source_rows <- integer()
used_agents <- character()
add_coverage <- function(code, guideline, agent, method, status, reason, ids, context) {
  coverage[[length(coverage) + 1L]] <<- data.frame(
    organism_code = code, guideline = guideline, antibiotic_code = agent,
    method = method, clinical_context = context, status = status, reason = reason,
    source_rows = paste(sort(ids), collapse = ";"), stringsAsFactors = FALSE
  )
}
valid_mic <- suppressWarnings(as.numeric(as.character(AMR:::VALID_MIC_LEVELS)))
valid_mic <- sort(unique(valid_mic[is.finite(valid_mic) & valid_mic > 0]))
for (code in organism_codes) {
  organism <- mo[as.character(mo$mo) == code, , drop = FALSE]
  ancestor_names <- unlist(organism[c("genus", "family", "order", "class")])
  ancestors <- as.character(mo$mo[match(ancestor_names, mo$fullname)])
  groups <- as.character(microorganisms.groups$mo_group[
    as.character(microorganisms.groups$mo) == code
  ])
  gram <- mo_gramstain(code, language = "en")
  broad <- if (identical(gram, "Gram-negative")) "B_GRAMN" else if (identical(gram, "Gram-positive")) "B_GRAMP" else character()
  if (identical(organism$oxygen_tolerance, "anaerobe")) {
    broad <- c(broad, "B_ANAER", if (identical(gram, "Gram-negative")) "B_ANAER-NEG" else "B_ANAER-POS")
  }
  relevant <- bp[as.character(bp$mo) %in% c(code, ancestors, groups, broad, "UNKNOWN"), , drop = FALSE]
  used_source_rows <- union(used_source_rows, relevant$source_row)
  for (guideline in guidelines) {
    current <- relevant[relevant$guideline == guideline, , drop = FALSE]
    for (agent in sort(unique(as.character(current$ab)))) {
      used_agents <- union(used_agents, agent)
      for (method in sort(unique(current$method[as.character(current$ab) == agent]))) {
        candidates <- current[as.character(current$ab) == agent & current$method == method, , drop = FALSE]
        ranked <- candidates[candidates$rank_index == min(candidates$rank_index), , drop = FALSE]
        for (context in c("", "NON_MENINGITIS")) {
        best <- ranked
        if (context == "NON_MENINGITIS") {
          best <- best[!is.na(best$site) & best$site == "Non-meningitis", , drop = FALSE]
          if (!nrow(best)) next
        }
        reason <- ""
        signatures <- unique(best[c("breakpoint_S", "breakpoint_R", "is_SDD", "note", "site", "uti", "disk_dose")])
        if (nrow(signatures) != 1L) reason <- "ambiguous_same_rank_rules"
        if (any(best$uti %in% TRUE | (!empty(best$site) & context == ""))) reason <- "site_route_or_uti_context_not_supported"
        if (any(best$is_SDD %in% TRUE)) reason <- "sdd_not_supported"
        if (any(is.na(best$breakpoint_S) | is.na(best$breakpoint_R))) reason <- "no_or_incomplete_breakpoint"
        if (any(!empty(best$note))) reason <- "conditional_note_requires_review"
        rule <- best[order(best$source_row), , drop = FALSE][1L, , drop = FALSE]
        s <- rule$breakpoint_S
        r <- rule$breakpoint_R
        mic_method <- method == "MIC"
        publisher <- sub(" .*", "", guideline)
        if (mic_method && any(best$breakpoint_S <= 0.001, na.rm = TRUE)) reason <- "no_susceptible_category"
        if (reason == "" && (!is.finite(s) || !is.finite(r) || s <= 0 || r <= 0 ||
            (mic_method && s > r) || (!mic_method && s < r))) reason <- "unrepresentable_thresholds"
        # A zone breakpoint holds only for the disk content it was set for.
        disk <- unique(as.character(best$disk_dose[!empty(best$disk_dose)]))
        if (reason == "" && !mic_method && length(disk) != 1L) reason <- "disk_content_missing_or_ambiguous"
        if (reason == "" && !mic_method) {
          points <- sort(unique(c(s, r, s - 1, s + 1, r - 1, r + 1, 6:50)))
          points <- points[is.finite(points) & points >= 0 & points == round(points)]
        } else if (reason == "") {
          points <- unique(c(s, r, 2^(-10:10), 1.5, 3, 6, 12))
          for (boundary in c(s, r)) {
            points <- c(points, tail(valid_mic[valid_mic < boundary], 1L), head(valid_mic[valid_mic > boundary], 1L))
          }
          points <- sort(unique(points[is.finite(points) & points > 0]))
          mic <- suppressWarnings(as.mic(points))
          keep <- !is.na(mic) & as.numeric(mic) == points
          points <- points[keep]
        } else {
          points <- c(1, 2)
        }
        edition <- paste(sub("^[A-Z]+ ", "", guideline), version, sep = "-")
        rows[[length(rows) + 1L]] <- data.frame(
          publisher = publisher, version = edition, clinical_context = context,
          organism_or_group = organism$fullname, antibiotic_whonet_code = agent,
          method = if (mic_method) "MIC" else "ZONE", specimen_type_id = "",
          breakpoint_type = if (mic_method) "MIC" else "ZONE",
          disk_content = if (!mic_method && length(disk) == 1L) disk else "",
          susceptible_value = if (reason == "") s else NA_real_, intermediate_lower_value = "", intermediate_upper_value = "",
          resistant_value = if (reason == "") r else NA_real_,
          units = if (mic_method) "mg/L" else "mm",
          resistant_inclusive = if (publisher == "CLSI") "true" else "false",
          organism_source = "AMR", organism_code = code,
          antibiotic_source = "AMR", antibiotic_code = agent,
          notes = paste("AMR source row", rule$source_row, "target", as.character(rule$mo),
            if (reason != "") paste("NO_BREAKPOINT:", reason) else "",
            if (!empty(rule$note)) rule$note else ""),
          stringsAsFactors = FALSE
        )
        vectors[[length(vectors) + 1L]] <- data.frame(
          publisher = publisher, version = edition, clinical_context = context, organism_code = code,
          antibiotic_code = agent, method = if (mic_method) "MIC" else "ZONE", value = points,
          expected = if (reason == "") "PENDING" else "NO_BREAKPOINT", stringsAsFactors = FALSE
        )
        add_coverage(code, guideline, agent, if (mic_method) "MIC" else "ZONE",
          if (reason == "") "SUPPORTED" else "BLOCKED", reason, candidates$source_row, context)
        }
      }
    }
  }
}
if (!length(rows)) stop("Scope produces no supported breakpoints")
rules <- do.call(rbind, rows)
checks <- do.call(rbind, vectors)
coverage <- do.call(rbind, coverage)
context_key <- function(x) paste(x$publisher, x$version, x$organism_code, x$antibiotic_code, x$method, x$clinical_context, sep = "|")
rule_keys <- context_key(rules)
check_keys <- context_key(checks)
# Batch the real source interpreter by edition, retaining its CLSI rounding and
# matching behaviour. No custom reference_data bypasses the source engine.
for (guideline in guidelines) {
  message("Verifying ", guideline, " with the source interpreter")
  publisher <- sub(" .*", "", guideline)
  edition <- paste(sub("^[A-Z]+ ", "", guideline), version, sep = "-")
  selected <- which(checks$publisher == publisher & checks$version == edition & checks$expected == "PENDING")
  if (!length(selected)) next
  values <- checks$value[selected]
  is_mic <- checks$method[selected] == "MIC"
  interpret <- function(observed, indices) as.character(suppressMessages(suppressWarnings(as.sir(
    observed,
    mo = checks$organism_code[indices],
    ab = checks$antibiotic_code[indices],
    guideline = guideline,
    uti = FALSE,
    include_screening = FALSE,
    include_PKPD = FALSE,
    add_intrinsic_resistance = FALSE,
    info = FALSE
  ))))
  # The public interpreter appends every observation to its session history.
  # Bounded batches avoid repeatedly copying the entire edition's history.
  expected <- character(length(selected))
  for (batch in split(seq_along(selected), ceiling(seq_along(selected) / 1000L))) {
    mic_part <- batch[is_mic[batch]]
    zone_part <- batch[!is_mic[batch]]
    if (length(mic_part)) {
      invisible(sir_interpretation_history(clean = TRUE))
      expected[mic_part] <- interpret(as.mic(values[mic_part]), selected[mic_part])
    }
    if (length(zone_part)) {
      invisible(sir_interpretation_history(clean = TRUE))
      expected[zone_part] <- interpret(as.disk(values[zone_part]), selected[zone_part])
    }
  }
  invisible(sir_interpretation_history(clean = TRUE))
  matched <- match(check_keys[selected], rule_keys)
  evaluated <- values
  if (publisher == "CLSI") {
    ladder <- as.numeric(AMR:::COMMON_MIC_VALUES)
    evaluated[is_mic] <- vapply(values[is_mic], function(x) if (any(ladder >= x)) min(ladder[ladder >= x]) else x, numeric(1))
  }
  susceptible <- rules$susceptible_value[matched]
  resistant <- rules$resistant_value[matched]
  # MIC: lower is more susceptible. Zone: a larger diameter is more susceptible.
  translated <- ifelse(is_mic,
    ifelse(evaluated <= susceptible, "S",
      ifelse(if (publisher == "CLSI") evaluated >= resistant else evaluated > resistant, "R", "I")),
    ifelse(evaluated >= susceptible, "S",
      ifelse(if (publisher == "CLSI") evaluated <= resistant else evaluated < resistant, "R", "I")))
  mismatch <- is.na(expected) | expected != translated
  blocked <- unique(check_keys[selected][mismatch])
  if (length(blocked)) {
    changed <- rule_keys %in% blocked
    rules$susceptible_value[changed] <- NA_real_
    rules$resistant_value[changed] <- NA_real_
    rules$notes[changed] <- paste(rules$notes[changed], "NO_BREAKPOINT: source_interpretation_mismatch")
    coverage$status[changed] <- "BLOCKED"
    coverage$reason[changed] <- "source_interpretation_mismatch"
  }
  checks$expected[selected] <- expected
  checks$expected[check_keys %in% blocked] <- "NO_BREAKPOINT"
}
if (any(checks$expected == "PENDING" | is.na(checks$expected))) stop("Unverified rule")
if (!all(paste(sub("^[A-Z]+ ", "", guidelines), version, sep = "-") %in% rules$version)) stop("An edition has no rules")
# Breakpoint rules stay expanded for the rule scope only. The concept list can be
# every accepted taxon, so an identification outside the rule scope is still a
# known organism that reads "No breakpoint", not a missing choice.
listed_codes <- organism_codes
if (organism_list == "accepted-taxa") {
  listed_codes <- sort(unique(c(organism_codes, as.character(mo$mo[mo$status == "accepted"]))))
}
organisms <- mo[match(listed_codes, as.character(mo$mo)), , drop = FALSE]
# WHONET labels its genus-level entry "<genus> sp."; every other entry carries the
# taxon's own name. A label that names more than one code is not used.
labelled <- microorganisms.codes[grepl("^WHONET \\(.*\\)$", microorganisms.codes$source), , drop = FALSE]
label_keys <- paste(as.character(labelled$mo), sub("^WHONET \\((.*)\\)$", "\\1", labelled$source), sep = "|")
ambiguous <- unique(label_keys[duplicated(label_keys)])
own_name <- paste(listed_codes, organisms$fullname, sep = "|")
genus_name <- ifelse(organisms$rank == "genus", paste0(own_name, " sp."), NA_character_)
whonet_row <- match(genus_name, label_keys)
whonet_row[is.na(whonet_row)] <- match(own_name, label_keys)[is.na(whonet_row)]
whonet_row[label_keys[whonet_row] %in% ambiguous] <- NA_integer_
whonet <- ifelse(is.na(whonet_row), "", tolower(as.character(labelled$code[whonet_row])))
whonet_name <- ifelse(is.na(whonet_row), "", sub("^[^|]*\\|", "", label_keys[whonet_row]))
if (anyDuplicated(whonet[whonet != ""])) stop("A WHONET code names two organisms")
# The nearest group above species that breakpoint rules target for each
# organism: a named group (for example Streptococcus Group A), else its genus,
# family, order or class. Blank when no rule targets any of them.
targets <- unique(as.character(bp$mo))
target_names <- as.character(mo$fullname[match(targets, as.character(mo$mo))])
targeted_groups <- microorganisms.groups[as.character(microorganisms.groups$mo_group) %in% targets, , drop = FALSE]
groups_by_member <- split(as.character(targeted_groups$mo_group), as.character(targeted_groups$mo))
organism_group <- vapply(seq_along(listed_codes), function(i) {
  groups <- unique(groups_by_member[[listed_codes[i]]])
  if (length(groups) == 1L) return(as.character(mo$fullname[match(groups, as.character(mo$mo))]))
  for (rank in c("genus", "family", "order", "class")) {
    name <- as.character(organisms[[rank]][i])
    if (empty(name) || name == as.character(organisms$fullname[i])) next
    if (name %in% target_names) return(name)
  }
  ""
}, character(1), USE.NAMES = FALSE)
write_csv(
  data.frame(
    source = "AMR", code = listed_codes, displayName = organisms$fullname,
    whonetCode = whonet, whonetName = whonet_name, organism_group = organism_group,
    gramStain = toupper(gsub("-", "_", mo_gramstain(listed_codes, language = "en"))),
    rank = organisms$rank, prevalence = organisms$prevalence,
    stringsAsFactors = FALSE
  ),
  "organisms.csv"
)
agents <- ab[match(sort(used_agents), as.character(ab$ab)), , drop = FALSE]
if (any(is.na(agents$ab))) stop("Unresolved antimicrobial")
write_csv(
  data.frame(
    source = "AMR", code = as.character(agents$ab), displayName = agents$name,
    whonetCode = as.character(agents$ab), antibioticClass = agents$atc_group2,
    stringsAsFactors = FALSE
  ),
  "antimicrobials.csv"
)
write_csv(rules, "breakpoints.csv")
write_csv(checks, "interpretation-vectors.csv")
write_csv(coverage, "coverage.csv")
# Every selected source row remains accounted for, including blockers and out-of-scope taxa.
write_csv(
  data.frame(
    source_row = bp$source_row, guideline = bp$guideline,
    organism_code = as.character(bp$mo), antibiotic_code = as.character(bp$ab),
    method = bp$method,
    status = ifelse(bp$source_row %in% used_source_rows, "SEE_COVERAGE", "OUTSIDE_ORGANISM_SCOPE"),
    stringsAsFactors = FALSE
  ),
  "source-rows.csv"
)
