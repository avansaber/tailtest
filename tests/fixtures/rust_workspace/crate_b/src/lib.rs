use crate_a::greet;

pub fn shout(name: &str) -> String {
    greet(name).to_uppercase()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_shout() {
        assert_eq!(shout("world"), "HELLO, WORLD!");
    }
}
