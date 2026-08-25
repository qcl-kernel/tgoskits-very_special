/* Temporary, opt-in early-handoff markers for ATK-DLRK3588 bring-up. */

	.section .text

	.macro emit_uart_marker name, character
	.global \name
	.type \name, %function
\name:
	movz x9, #0
	movk x9, #0xfeb5, lsl #16
1:
	ldr w10, [x9, #0x14]
	tbz w10, #5, 1b
	mov w10, #\character
	str w10, [x9]
	ret
	.size \name, . - \name
	.endm

	emit_uart_marker z_arm64_el2_plat_prep_c, 'P'
	emit_uart_marker z_arm64_el_highest_plat_init, 'H'
	emit_uart_marker z_arm64_el2_plat_init, '2'
	emit_uart_marker z_arm64_el1_plat_init, '1'

	.global atk_early_uart_marker
	.type atk_early_uart_marker, %function
atk_early_uart_marker:
	mov w11, w0
	movz x9, #0
	movk x9, #0xfeb5, lsl #16
1:
	ldr w10, [x9, #0x14]
	tbz w10, #5, 1b
	str w11, [x9]
	ret
	.size atk_early_uart_marker, . - atk_early_uart_marker
