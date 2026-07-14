/// Keep the first and last `keep` lines; drop the middle (where stack traces rarely hide) with a marker.
pub fn head_tail(text: &str, keep: usize) -> (String, bool) {
    let lines: Vec<&str> = text.lines().collect();
    if keep == 0 || lines.len() <= keep.saturating_mul(2) {
        return (text.to_string(), false);
    }
    let head = &lines[..keep];
    let tail = &lines[lines.len() - keep..];
    let omitted = lines.len() - keep * 2;
    let mut out = head.join("\n");
    out.push_str(&format!("\n...[tokendog: {} lines omitted]...\n", omitted));
    out.push_str(&tail.join("\n"));
    (out, true)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keeps_head_and_tail() {
        let text = (0..1000).map(|i| i.to_string()).collect::<Vec<_>>().join("\n");
        let (out, cut) = head_tail(&text, 10);
        assert!(cut);
        let lines: Vec<&str> = out.lines().collect();
        assert_eq!(lines[0], "0");
        assert_eq!(lines[lines.len() - 1], "999");
        assert!(out.contains("omitted"));
    }

    #[test]
    fn short_text_unchanged() {
        let (out, cut) = head_tail("a\nb\nc", 10);
        assert_eq!(out, "a\nb\nc");
        assert!(!cut);
    }

    #[test]
    fn zero_keep_is_noop() {
        // keep=0 is a no-op sentinel (guard against misconfiguration); returns text unchanged, not empty
        let text = (0..10).map(|i| i.to_string()).collect::<Vec<_>>().join("\n");
        let (out, cut) = head_tail(&text, 0);
        assert_eq!(out, text);
        assert!(!cut);
    }
}
