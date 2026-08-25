//! Decides when an acknowledged host CNTV PPI still has a Guest retirement path.

/// Whether the host CNTV activation must remain owned by the timer binding.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum HostActivationDisposition {
    /// Keep the host activation until the corresponding VGIC PPI retires.
    HoldForGuestRetirement,
    /// Complete the host activation because no Guest PPI can retire it.
    RetireImmediately,
}

/// Derives host activation ownership from the published virtual-timer level.
pub(crate) const fn host_activation_disposition(
    virtual_timer_asserted: bool,
) -> HostActivationDisposition {
    if virtual_timer_asserted {
        HostActivationDisposition::HoldForGuestRetirement
    } else {
        HostActivationDisposition::RetireImmediately
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn retires_host_activation_when_guest_virtual_timer_is_disabled() {
        assert_eq!(
            host_activation_disposition(false),
            HostActivationDisposition::RetireImmediately
        );
    }

    #[test]
    fn holds_host_activation_while_guest_virtual_timer_is_asserted() {
        assert_eq!(
            host_activation_disposition(true),
            HostActivationDisposition::HoldForGuestRetirement
        );
    }
}
