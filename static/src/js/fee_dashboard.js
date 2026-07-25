/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart, useState } from "@odoo/owl";

function localDateStr(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
}

export class FeeDashboard extends Component {
    static template = "quick_pay.FeeDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        const today = localDateStr(new Date());
        this.state = useState({
            loading: true,
            dateFrom: today,
            dateTo: today,
            activeRange: "today",
            data: null,
            error: "",
        });
        onWillStart(() => this.loadData());
    }

    // ── data ────────────────────────────────────────────────────────
    async loadData() {
        this.state.loading = true;
        this.state.error = "";
        try {
            this.state.data = await this.orm.call(
                "quick.pay",
                "get_fee_report",
                [this.state.dateFrom, this.state.dateTo]
            );
            this.state.dateFrom = this.state.data.date_from;
            this.state.dateTo = this.state.data.date_to;
        } catch (e) {
            console.error("Fee report load failed", e);
            this.state.error = "Failed to load fee report. Please refresh.";
        } finally {
            this.state.loading = false;
        }
    }

    onDateFromChange(ev) {
        this.state.dateFrom = ev.target.value;
        this.state.activeRange = "";
    }

    onDateToChange(ev) {
        this.state.dateTo = ev.target.value;
        this.state.activeRange = "";
    }

    applyFilter() {
        if (!this.state.dateFrom || !this.state.dateTo) {
            return;
        }
        this.loadData();
    }

    setQuickRange(range) {
        const now = new Date();
        let from = new Date(now);
        let to = new Date(now);
        if (range === "yesterday") {
            from.setDate(now.getDate() - 1);
            to.setDate(now.getDate() - 1);
        } else if (range === "week") {
            from.setDate(now.getDate() - 6);
        } else if (range === "month") {
            from = new Date(now.getFullYear(), now.getMonth(), 1);
        }
        this.state.dateFrom = localDateStr(from);
        this.state.dateTo = localDateStr(to);
        this.state.activeRange = range;
        this.loadData();
    }

    // ── computed ────────────────────────────────────────────────────
    get summary() {
        return (this.state.data && this.state.data.summary) || {};
    }

    get batches() {
        return (this.state.data && this.state.data.batches) || [];
    }

    get daywise() {
        return (this.state.data && this.state.data.daywise) || [];
    }

    get maxDayTotal() {
        return Math.max(1, ...this.daywise.map((d) => d.total_collected));
    }

    fmt(n) {
        return (n || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    // ── drill-down ──────────────────────────────────────────────────
    openBatchRequests(batch) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `Quick Pay — ${batch.batch_name}`,
            res_model: "quick.pay",
            view_mode: "list,form",
            views: [[false, "list"], [false, "form"]],
            domain: [
                ["batch_id", "=", batch.batch_id],
                ["state", "=", "converted"],
                ["verified_date", ">=", `${this.state.dateFrom} 00:00:00`],
                ["verified_date", "<=", `${this.state.dateTo} 23:59:59`],
            ],
            target: "current",
        });
    }

    openPending() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Pending Payments",
            res_model: "quick.pay",
            view_mode: "list,form",
            views: [[false, "list"], [false, "form"]],
            domain: [["state", "=", "submitted"]],
            target: "current",
        });
    }

    openAllBalances() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Outstanding Balance — All Batches",
            res_model: "student.enrollment",
            view_mode: "list,form",
            views: [[false, "list"], [false, "form"]],
            domain: [["due_amount", ">", 0]],
            target: "current",
        });
    }

    openBatchBalance(batch, ev) {
        if (ev) {
            ev.stopPropagation();
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `Balance Due — ${batch.batch_name}`,
            res_model: "student.enrollment",
            view_mode: "list,form",
            views: [[false, "list"], [false, "form"]],
            domain: [
                ["batch_id", "=", batch.batch_id],
                ["due_amount", ">", 0],
            ],
            target: "current",
        });
    }
}

registry
    .category("actions")
    .add("quick_pay.fee_dashboard", FeeDashboard);
