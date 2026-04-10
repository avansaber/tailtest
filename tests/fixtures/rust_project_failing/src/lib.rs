pub fn subtract(a: i32, b: i32) -> i32 {
    a + b // bug: should be a - b
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_subtract() {
        assert_eq!(subtract(5, 3), 2); // will fail
    }

    #[test]
    fn test_subtract_zero() {
        assert_eq!(subtract(5, 0), 5); // passes
    }
}
