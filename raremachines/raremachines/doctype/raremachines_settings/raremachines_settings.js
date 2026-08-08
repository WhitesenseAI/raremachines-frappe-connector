// Copyright (c) 2026, Whitefield Labs and contributors
// For license information, please see license.txt

// Closed allowlist of pair error codes, mirroring ALLOWED_ERROR_CODES in
// `raremachines/api/connect.py`. The server already clamps every code it
// emits to that set, so anything arriving here outside it did not come from
// RareMachine — it came from whoever crafted the URL.
//
// SECURITY: `pair_error` is read straight from `window.location.search`, and
// `frappe.msgprint` renders its `message` with a raw jQuery `.append()`
// (frappe/public/js/frappe/ui/messages.js), which parses HTML. Interpolating
// the parameter — even via `__()`, which does not escape — is a reflected XSS,
// and because only System Managers can open this form the victim always holds
// full desk privileges. So the raw value is NEVER rendered: an unrecognised
// code falls back to a fixed string and the code itself is dropped.
const PAIR_ERROR_MESSAGES = {
	SITE_MISMATCH:
		"That RareMachine workspace is already linked to a different Frappe site. On RareMachine, disconnect or check Replace, then try again.",
	PAIRING_EXPIRED: "Connect link expired. Click Connect with RareMachine again.",
	NETWORK_UNREACHABLE:
		"Could not reach RareMachine. Check that this site has outbound internet access, then try again.",
	UNAUTHORIZED: "You need the System Manager role on this site to connect RareMachine.",
	CONFIG_INVALID:
		"RareMachine is not configured correctly on this site. Contact RareMachine support.",
	SSRF_REJECTED: "RareMachine rejected this site's URL. It must be a public https address.",
	KMS_MISCONFIGURED:
		"RareMachine could not encrypt this site's credentials. Contact RareMachine support.",
	SITE_ALREADY_LINKED:
		"This Frappe site is already linked to a different RareMachine workspace. Ask that workspace's admin to disconnect it, or join that workspace instead of linking the site again.",
	SITE_VERIFICATION_FAILED:
		"RareMachine could not reach this site at the URL it was given. Check that the site is reachable from the internet, then try again.",
	CONNECT_FAILED: "Could not connect to RareMachine. Please try again.",
};

frappe.ui.form.on("RareMachines Settings", {
	refresh(frm) {
		frappe.call({
			method: "raremachines.api.connect.get_pair_defaults",
			callback(r) {
				if (r.message && r.message.conduit_base_url) {
					frm.set_value("conduit_base_url", r.message.conduit_base_url);
					frm.refresh_field("conduit_base_url");
				}
			},
		});

		const params = new URLSearchParams(window.location.search || "");
		if (params.get("paired") === "1") {
			frappe.show_alert({
				message: __(
					"Connected to RareMachine. Each user still connects their own account in RareMachine for chat."
				),
				indicator: "green",
			});
			if (window.history && window.history.replaceState) {
				window.history.replaceState({}, "", "/app/raremachines-settings");
			}
		}
		const pairErr = params.get("pair_error");
		if (pairErr) {
			// Look the code up; never render it. See PAIR_ERROR_MESSAGES above.
			const known = Object.prototype.hasOwnProperty.call(PAIR_ERROR_MESSAGES, pairErr)
				? PAIR_ERROR_MESSAGES[pairErr]
				: "Could not connect to RareMachine. Please try again.";
			frappe.msgprint({
				title: __("Could not connect"),
				indicator: "red",
				message: __(known),
			});
			if (window.history && window.history.replaceState) {
				window.history.replaceState({}, "", "/app/raremachines-settings");
			}
		}

		frm.clear_custom_buttons();

		if (frm.doc.connection_status !== "Active") {
			frm.add_custom_button(__("Connect with RareMachine"), () => {
				frappe.call({
					method: "raremachines.api.connect.begin_connect_with_conduit",
					freeze: true,
					freeze_message: __("Opening RareMachine…"),
					callback(r) {
						const msg = r.message || {};
						if (msg.ok && msg.connect_url) {
							window.location.href = msg.connect_url;
						} else {
							frappe.msgprint(__("Could not start RareMachine connect."));
						}
					},
				});
			}).addClass("btn-primary");
		}

		frm.add_custom_button(__("Disconnect"), () => {
			frappe.confirm(
				__(
					"Disconnect RareMachine on this site? Users will need to reconnect after you pair again."
				),
				() => {
					frappe.call({
						method: "raremachines.api.connect.disconnect",
						freeze: true,
						callback(r) {
							if (r.message && r.message.ok) {
								frappe.show_alert({
									message: __("Disconnected"),
									indicator: "green",
								});
								frm.reload_doc();
							}
						},
					});
				}
			);
		});
	},
});
