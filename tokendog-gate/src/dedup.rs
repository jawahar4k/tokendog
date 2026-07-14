use std::collections::HashSet;

/// Drop exact-duplicate message contents (keep first occurrence).
pub fn dedup_contents(contents: &[String]) -> (Vec<String>, usize) {
    let mut seen = HashSet::new();
    let mut out = Vec::new();
    for c in contents {
        if seen.insert(c.as_str().to_owned()) {
            out.push(c.clone());
        }
    }
    let removed = contents.len() - out.len();
    (out, removed)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn removes_exact_duplicates_preserving_order() {
        let input = vec!["a".to_string(), "b".to_string(), "a".to_string(),
                         "c".to_string(), "b".to_string()];
        let (out, removed) = dedup_contents(&input);
        assert_eq!(out, vec!["a", "b", "c"]);
        assert_eq!(removed, 2);
    }

    #[test]
    fn empty_is_empty() {
        let (out, removed) = dedup_contents(&[]);
        assert!(out.is_empty() && removed == 0);
    }
}
