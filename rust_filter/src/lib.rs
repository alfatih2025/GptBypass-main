use aho_corasick::{AhoCorasick, AhoCorasickBuilder, MatchKind};
use pyo3::prelude::*;

fn normalize_patterns(patterns: Vec<String>) -> Vec<String> {
    patterns
        .into_iter()
        .map(|item| item.trim().to_string())
        .filter(|item| !item.is_empty())
        .collect()
}

fn build_matcher(patterns: &[String]) -> PyResult<AhoCorasick> {
    let refs: Vec<&str> = patterns.iter().map(|item| item.as_str()).collect();
    AhoCorasickBuilder::new()
        .ascii_case_insensitive(true)
        .match_kind(MatchKind::LeftmostLongest)
        .build(refs)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("构建匹配器失败: {e}")))
}

/// 检查给定文本是否包含任何敏感关键词。
#[pyfunction]
fn contains_sensitive_words(text: String, patterns: Vec<String>) -> PyResult<bool> {
    let patterns = normalize_patterns(patterns);
    if patterns.is_empty() {
        return Ok(false);
    }

    let ac = build_matcher(&patterns)?;
    Ok(ac.is_match(&text))
}

/// 返回文本中命中的所有敏感关键词（去重后按出现顺序返回）。
#[pyfunction]
fn find_sensitive_words(text: String, patterns: Vec<String>) -> PyResult<Vec<String>> {
    let patterns = normalize_patterns(patterns);
    if patterns.is_empty() {
        return Ok(Vec::new());
    }

    let ac = build_matcher(&patterns)?;

    let mut matched: Vec<String> = Vec::new();

    for mat in ac.find_iter(&text) {
        let pattern = patterns[mat.pattern().as_usize()].clone();
        if !matched.iter().any(|item| item == &pattern) {
            matched.push(pattern);
        }
    }

    Ok(matched)
}

/// A Python module implemented in Rust.
#[pymodule]
fn rust_filter(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(contains_sensitive_words, m)?)?;
    m.add_function(wrap_pyfunction!(find_sensitive_words, m)?)?;
    Ok(())
}
