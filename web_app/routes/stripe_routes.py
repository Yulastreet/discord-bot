"""Stripe Checkout + Customer Portal + Webhook for TookBot+.

Required env vars (.env):
    STRIPE_SECRET_KEY        sk_live_... or sk_test_...
    STRIPE_WEBHOOK_SECRET    whsec_... (copied from Stripe Dashboard > Webhooks endpoint)
    STRIPE_PRICE_1MO         price_xxx (4.39 EUR /month)
    STRIPE_PRICE_3MO         price_xxx (3.99 EUR /month, billed every 3 months)
    STRIPE_PRICE_6MO         price_xxx (3.49 EUR /month, billed every 6 months)
    STRIPE_PRICE_12MO        price_xxx (2.99 EUR /month, billed every 12 months)
    DASHBOARD_URL            https://dashboard.tookbot.click (for success/cancel/return URLs)

On the Stripe Dashboard side (https://dashboard.stripe.com):
1. Create 4 products "TookBot+ 1 month / 3 months / 6 months / 12 months"
2. For each one, add 1 recurring EUR price with the matching billing period
3. Configure your webhook endpoint: https://dashboard.tookbot.click/api/stripe/webhook
   Events to listen to: checkout.session.completed, customer.subscription.created,
   customer.subscription.updated, customer.subscription.deleted, invoice.paid, invoice.payment_failed
4. Enable the Customer Portal (Stripe Dashboard > Settings > Customer portal)
"""

import os
from flask import request, jsonify, g, redirect, session

from services.i18n import t

try:
    import stripe as _stripe
except ImportError:
    _stripe = None


_PRICE_BY_MONTHS = {
    "1":  "STRIPE_PRICE_1MO",
    "3":  "STRIPE_PRICE_3MO",
    "6":  "STRIPE_PRICE_6MO",
    "12": "STRIPE_PRICE_12MO",
}


def _g(obj, key, default=None):
    """Safe getter for a StripeObject (no dict-style .get since v15+).

    Tries __getitem__, then getattr, then default.
    """
    if obj is None:
        return default
    try:
        return obj[key]
    except (KeyError, TypeError):
        pass
    try:
        return getattr(obj, key, default)
    except AttributeError:
        return default


def _stripe_ready():
    if _stripe is None:
        return False, t("api.stripe.module_missing")
    key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    if not key:
        return False, t("api.stripe.secret_key_missing")
    _stripe.api_key = key
    return True, None


def register_stripe_routes(app, deps):
    globals().update(deps)
    from database import (
        stripe_subscription_upsert, stripe_subscription_get,
        stripe_subscription_get_by_customer, stripe_subscription_get_by_subscription,
        add_premium_grant, remove_premium_grant,
    )

    def _current_uid():
        if not g.discord_user:
            return None
        return g.discord_user.get("user_id") or g.discord_user.get("id")

    def _dashboard_base():
        return os.getenv("DASHBOARD_URL", "").rstrip("/") or request.host_url.rstrip("/")

    @app.route("/api/subscribe/checkout", methods=["POST"])
    def api_subscribe_checkout():
        ok, err = _stripe_ready()
        if not ok:
            return jsonify({"error": err}), 500
        uid = _current_uid()
        if not uid:
            return jsonify({"error": "not_logged_in"}), 401

        data = request.get_json(silent=True) or {}
        months = str(data.get("months") or "1").strip()
        price_env = _PRICE_BY_MONTHS.get(months)
        if not price_env:
            return jsonify({"error": t("api.stripe.invalid_months")}), 400
        price_id = os.getenv(price_env, "").strip()
        if not price_id:
            return jsonify({"error": t("api.stripe.price_env_missing", env_var=price_env)}), 500

        base = _dashboard_base()
        # Re-use the Stripe customer when the user already has one
        existing = stripe_subscription_get(uid) or {}
        customer = existing.get("stripe_customer_id") or None

        try:
            # In subscription mode Stripe creates a Customer automatically when none is given.
            # customer_creation is forbidden in subscription mode (payment one-shot only).
            kwargs = dict(
                mode="subscription",
                payment_method_types=["card", "paypal"],
                line_items=[{"price": price_id, "quantity": 1}],
                client_reference_id=str(uid),
                metadata={"discord_user_id": str(uid), "plan_months": months},
                subscription_data={
                    "metadata": {"discord_user_id": str(uid), "plan_months": months},
                },
                success_url=f"{base}/subscription?paid=1&session_id={{CHECKOUT_SESSION_ID}}",
                cancel_url=f"{base}/subscription?canceled=1",
                allow_promotion_codes=True,
                locale="fr",
            )
            if customer:
                kwargs["customer"] = customer
            sess = _stripe.checkout.Session.create(**kwargs)
        except Exception as e:
            print(f"[stripe checkout] err: {type(e).__name__}: {e}")
            return jsonify({"error": t("api.stripe.checkout_unavailable")}), 500

        return jsonify({"url": sess.url, "session_id": sess.id})

    @app.route("/api/subscribe/portal", methods=["POST"])
    def api_subscribe_portal():
        ok, err = _stripe_ready()
        if not ok:
            return jsonify({"error": err}), 500
        uid = _current_uid()
        if not uid:
            return jsonify({"error": "not_logged_in"}), 401

        sub = stripe_subscription_get(uid)
        if not sub or not sub.get("stripe_customer_id"):
            return jsonify({"error": t("api.stripe.no_subscription")}), 404

        base = _dashboard_base()
        try:
            portal = _stripe.billing_portal.Session.create(
                customer=sub["stripe_customer_id"],
                return_url=f"{base}/subscription",
                locale="fr",
            )
        except Exception as e:
            print(f"[stripe portal] err: {type(e).__name__}: {e}")
            return jsonify({"error": t("api.stripe.portal_unavailable")}), 500

        return jsonify({"url": portal.url})

    @app.route("/api/stripe/webhook", methods=["POST"])
    def api_stripe_webhook():
        ok, err = _stripe_ready()
        if not ok:
            return jsonify({"error": err}), 500
        wh_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
        if not wh_secret:
            return jsonify({"error": t("api.stripe.webhook_secret_missing")}), 500

        payload = request.get_data(as_text=False)
        sig = request.headers.get("Stripe-Signature", "")
        try:
            event = _stripe.Webhook.construct_event(payload, sig, wh_secret)
        except Exception as e:
            print(f"[stripe webhook] invalid signature: {e}")
            return jsonify({"error": t("api.stripe.invalid_signature")}), 400

        etype = event["type"]
        obj   = event["data"]["object"]
        print(f"[stripe webhook] event {etype}")

        try:
            if etype == "checkout.session.completed":
                _handle_checkout_completed(obj)
            elif etype in ("customer.subscription.created", "customer.subscription.updated"):
                _handle_subscription_updated(obj)
            elif etype == "customer.subscription.deleted":
                _handle_subscription_deleted(obj)
            elif etype == "invoice.paid":
                _handle_invoice_paid(obj)
            elif etype == "invoice.payment_failed":
                _handle_invoice_failed(obj)
        except Exception as e:
            import traceback; traceback.print_exc()
            return jsonify({"error": f"handler error: {type(e).__name__}"}), 500

        return jsonify({"ok": True})

    def _handle_checkout_completed(sess):
        """First successful payment: store customer + subscription + grant tookbot_plus."""
        meta      = _g(sess, "metadata") or {}
        uid       = _g(meta, "discord_user_id") or _g(sess, "client_reference_id")
        months    = int(_g(meta, "plan_months") or 1)
        customer  = _g(sess, "customer")
        sub_id    = _g(sess, "subscription")
        if not uid:
            print("[stripe checkout.completed] missing discord_user_id, skipping")
            return
        stripe_subscription_upsert(
            uid,
            stripe_customer_id=customer,
            stripe_subscription_id=sub_id,
            plan_months=months,
            status="active",
        )
        add_premium_grant(uid, feature="tookbot_plus", granted_by="stripe",
                          note=f"sub:{sub_id} plan:{months}mo")
        print(f"[stripe] grant tookbot_plus for user={uid} sub={sub_id} ({months}mo)")

    def _handle_subscription_updated(sub):
        """Subscription state change: sync the DB + grant/revoke depending on status."""
        meta     = _g(sub, "metadata") or {}
        uid_meta = _g(meta, "discord_user_id")
        customer = _g(sub, "customer")
        sub_id   = _g(sub, "id")
        status   = _g(sub, "status")
        cpe      = _g(sub, "current_period_end")
        # Resolve uid through metadata or a DB lookup
        uid = uid_meta
        if not uid and customer:
            row = stripe_subscription_get_by_customer(customer) or {}
            uid = row.get("discord_user_id")
        if not uid:
            print(f"[stripe sub.updated] uid not found customer={customer}")
            return
        stripe_subscription_upsert(
            uid,
            stripe_customer_id=customer,
            stripe_subscription_id=sub_id,
            status=status,
            current_period_end=cpe,
        )
        if status in ("active", "trialing"):
            add_premium_grant(uid, feature="tookbot_plus", granted_by="stripe",
                              note=f"sub:{sub_id} status:{status}")
        else:
            remove_premium_grant(uid, feature="tookbot_plus")
        print(f"[stripe sub.updated] uid={uid} status={status}")

    def _handle_subscription_deleted(sub):
        meta     = _g(sub, "metadata") or {}
        uid_meta = _g(meta, "discord_user_id")
        customer = _g(sub, "customer")
        uid = uid_meta
        if not uid and customer:
            row = stripe_subscription_get_by_customer(customer) or {}
            uid = row.get("discord_user_id")
        if not uid:
            return
        stripe_subscription_upsert(uid, status="canceled")
        remove_premium_grant(uid, feature="tookbot_plus")
        print(f"[stripe sub.deleted] revoke tookbot_plus uid={uid}")

    def _handle_invoice_paid(inv):
        # Refresh current_period_end through the subscription
        sub_id = _g(inv, "subscription")
        if not sub_id:
            return
        try:
            sub = _stripe.Subscription.retrieve(sub_id)
            _handle_subscription_updated(sub)
        except Exception as e:
            print(f"[stripe invoice.paid] retrieve fail: {e}")

    def _handle_invoice_failed(inv):
        sub_id = _g(inv, "subscription")
        if not sub_id:
            return
        row = stripe_subscription_get_by_subscription(sub_id) or {}
        uid = row.get("discord_user_id")
        if uid:
            stripe_subscription_upsert(uid, status="past_due")
            # Keep the grant for a few days (Stripe will retry), no immediate revoke
            print(f"[stripe invoice.failed] uid={uid} status=past_due")
