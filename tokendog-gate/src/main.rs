use std::io::{self, Read, Write};

fn main() {
    let mut input = String::new();
    if io::stdin().read_to_string(&mut input).is_err() {
        return;
    }
    let out = tokendog_gate::transform(&input);
    let _ = io::stdout().write_all(out.as_bytes());
}
