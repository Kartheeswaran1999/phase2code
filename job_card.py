import time
from odoo.exceptions import UserError
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError, RedirectWarning
from datetime import datetime, timedelta, time
from dateutil.relativedelta import relativedelta
import pytz
from num2words import num2words
from num2words.lang_EN import Num2Word_EN
from translate import Translator
from odoo.http import request
import qrcode
import base64
from io import BytesIO
import requests
import logging
import re
from geopy.geocoders import Nominatim
import math
from decimal import Decimal, ROUND_UP
from urllib.parse import urlparse, parse_qs, unquote
import urllib.parse
from lxml import etree
from collections import OrderedDict
import ast
import copy
from datetime import date

_logger = logging.getLogger(__name__)

'''
code   Job State
101    New
102    Scheduled (Technician Assigned)
103    Technician Accepted
104    Technician Rejected
105    Failed to attend call (Customer not answered)
106    Out of City
107    Rescheduled (Collect the re-schedule date & time @ the time of this request)
108    Customer Accepted
109    Technician Started
110    Technician Reached
111    Warranty Verification
112    Cancelled. Not Agree to Pay for Inspection
113    Inspection Started
114    Quotation provided. Waiting customer approval
115    Job Started (In-progress)
116    Payment Refused
117    Unit Pull Out
118    Unit Replaced
119    Unit Returned
120    Pending
121    On Hold - Spare Parts Required
122    Parts Ready
123    Parts Received
124    Cancelled
125    Ready to Invoice (Complete)
126    Closed

'''


def generate_qr_code(value):
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=20,
        border=4)
    qr.add_data(value)
    qr.make(fit=True)
    img = qr.make_image()
    stream = BytesIO()
    img.save(stream, format="PNG")
    qr_img = base64.b64encode(stream.getvalue())
    return qr_img


_task_type_cache = None


class ProjectTask(models.Model):
    _inherit = 'project.task'
    _description = 'Job Card'

    # _order = 'id desc'
    # _inherit = ['mail.thread', 'mail.activity.mixin', 'format.address.mixin', 'portal.mixin']

    @api.model
    def create(self, vals):
        # Force no Sales Order linkage
        vals['sale_order_id'] = False
        return super().create(vals)

    stage_kanban_color = fields.Char(
        string="Stage Kanban Color",
        compute='_compute_stage_kanban_color',
        store=False,
    )

    # @api.model
    # def _default_maintenance_tab_show_bool(self):
    #     bool_search = self.env['ir.config_parameter'].sudo().get_param(
    #         'machine_repair_management.maintenance_service_show')
    #     return bool_search

    @api.depends('job_state')
    def _compute_stage_kanban_color(self):
        for task in self:
            task.stage_kanban_color = task.job_state.kanban_color if task.job_state and hasattr(task.job_state,
                                                                                                'kanban_color') else '#FFFFFF'

    name = fields.Char(string='Job Card #', tracking=True, required=True, index='trigram')

    job_state = fields.Many2one(
        'project.task.type',
        string="Job Status",
        domain=lambda self: self._get_job_state_domain(),
        tracking=True,
        store=True,
    )

    project_id = fields.Many2one(
        'project.project',
        string='Project',
        default=lambda self: self.env['project.project'].search([('name', '=', 'HHS')], limit=1)
    )

    # Contract based update field Added on 15-11-2025

    maintenance_type = fields.Selection([('corrective', 'Corrective'), ('preventive', 'Preventive')], string='Job Type',
                                        default="corrective")

    contract_id = fields.Many2one('subscription.contracts', string="Contract No")
    contract_date = fields.Date(string="Contract Start Date")
    contract_expiry_date = fields.Date(string="Contract Expiry Date")
    asset_id = fields.Many2one(
        'maintenance.equipment',
        string="Equipment Tag No",
        domain="[('contract_id', '=', contract_id)]"  # Dynamic domain
    )

    service_products_code_id = fields.Many2one(
        'product.product',
        string="Service Unit Type",
        domain="[('detailed_type', '=', 'service')]",
    )

    actual_preventive = fields.Char(
        string="Actual Preventive",
    )

    actual_corrective = fields.Char(
        string="Actual Corrective",
    )
    paid_service_bool = fields.Boolean("Paid Service", default=False)

    amc_project_id = fields.Many2one(
        'project.project',
        string='Project',
    )

    project_related_amc_bool = fields.Boolean(string='Project AMC (Y/N)', default=False, store=True)

    team_id = fields.Many2one(
        'machine.support.team', search="_search_team_id",
        string='Team Leader', compute="_compute_team_id", store=True)

    work_center_id = fields.Many2one('work.center.location', string="Work Center")

    work_center_group_id = fields.Many2one('work.center.group', string="Work Center Group")

    invoice_date = fields.Date(string="Invoice Date")

    parts_total_amount = fields.Float(string="Parts Amount", compute="_compute_parts_total_amount", store=True)
    parts_vat_totamount = fields.Float(string="Parts VAT Amount", compute="_compute_parts_total_amount", store=True)
    parts_grand_total_amount = fields.Float(string="Parts Total", compute="_compute_parts_total_amount", store=True)

    service_charge_amount = fields.Float(string="Service Charge Amount", compute="_compute_parts_total_amount",
                                         store=True)
    service_vat_amount = fields.Float(string="Service VAT Amount", compute="_compute_parts_total_amount", store=True)
    service_grand_total_amount = fields.Float(string="Service Total", compute="_compute_parts_total_amount", store=True)

    region_id = fields.Many2one('res.region', string="Region")

    available_user_ids = fields.Many2many('res.users', compute='_compute_available_user_ids')

    service_request_id = fields.Many2one('machine.repair.support', string="Service Request Id")

    state_status = fields.Boolean(string="State Status", default=False, compute="_compute_state_status", store=True)

    job_card_state = fields.Char(string="Job Card State", store=True)

    technician_accepted_status_check = fields.Boolean(string="Technician Accepted Status", default=False,
                                                      help="when we change the Technician accepted Status")

    ready_to_invoice_status_check = fields.Boolean(string="Ready to Invoice Status", default=False,
                                                   help="When we Change the Ready to invoice Status")

    # job_card_state = fields.Char(string ="Job Card State",  compute = "_compute_job_card_state", store =True)

    job_card_state_code = fields.Char(string="Job Card State Code", store=True, index=True)

    export_bool = fields.Boolean(string="Export Bool", default=False)

    user_ids_bool = fields.Boolean(string="User id bool", default=False)

    technician_id = fields.Many2one('res.users', string="Technician Name", compute='_compute_technician_id',
                                    inverse='_inverse_technician_id', store=True)

    warehouse_code = fields.Char(string="Warehouse Code")

    warehouse_complete_name = fields.Char(string="Warehouse Complete Name", store=True,
                                          compute="_compute_warehouse_name")

    svc_id = fields.Many2one('service.capacity', string="Capacity", )

    capacity = fields.Char(string="Capacity")

    purchase_dealer_name = fields.Char(string="Dealer Name")

    whatsapp_scheduled_message_sent_bool = fields.Boolean('Whatsapp Scheduled Message', default=False,
                                                          help="Whatsapp scheduled message to customer and technician",
                                                          compute="_compute_whatsapp_scheduled_message_sent_bool",
                                                          store=True)

    cancel_status_check = fields.Boolean(string="Cancel Status Check", default=False)

    cancel_button_wizard_bool = fields.Boolean(string="Cancel Button Wizard", default=False)

    previous_job_card_state_code = fields.Char(string="Previous Job State Code")

    technician_first_visit = fields.Char(string="First Visit Name", store=True)

    technician_first_visit_datetime = fields.Datetime(string="Technician First Visit Datetime")

    technician_first_visit_date = fields.Date(string="First Visit Date", )

    technician_first_intime = fields.Char(string="First InTime", compute="_compute_technician_first_intime", store=True)

    technician_first_outtime = fields.Char(string="First OutTime")

    second_visit_technician_bool = fields.Boolean(string="Second Visit(Y/N)", default=False)

    technician_second_visit_datetime = fields.Datetime(string="Second Visit Datetime")

    technician_second_visit_date = fields.Date(string="Final Visit Date")

    technician_second_visit = fields.Char(string="Second Visit Name", store=True)

    technician_second_intime = fields.Char(string="Second InTime", compute="_compute_technician_second_intime",
                                           store=True)

    technician_second_outtime = fields.Char(string="Second OutTime")

    engineer_comments_second = fields.Text(string="Technician Comments")

    technician_first_visit_id = fields.Many2one('res.users', string="Technician First Visit Name")

    technician_second_visit_id = fields.Many2one('res.users', string="Technician Second Visit name")

    message_log_ids = fields.One2many(
        'project.task.message.log',
        'res_id',
        string='Message Logs',
        domain=[('model', '=', 'project.task')]
    )
    volt = fields.Float(string="Volt (V)")
    ampere = fields.Float(string='Ampere (A)')
    lp = fields.Integer(string='L/P (psi)')
    hp = fields.Integer(string='H/P (psi)')
    sat = fields.Float(string="S.A.T (C)")
    rat = fields.Integer(string='R.A.T (C)')
    length = fields.Integer(string='Length (m)')
    width = fields.Integer(string='Width (m)')
    area = fields.Integer(string="Area (sqm)", readonly=True, compute="_compute_area")
    p_length = fields.Integer(string="P/Length (m)")


    cancelled_inspection_charges_bool = fields.Boolean(string="Cancelled Inspection Charges", default=False)

    date_pick_warranty_expiry = fields.Selection([(str(d), str(d)) for d in range(1, 32)], string="Date Pick")

    # month_pick = fields.Selection([(str(m),str(m)) for m in range(1,13)],string = "Month Pick")
    month_pick_warranty_expiry = fields.Selection([('1', 'Jan'),
                                                   ('2', 'Feb'),
                                                   ('3', 'Mar'),
                                                   ('4', 'Apr'),
                                                   ('5', 'May'),
                                                   ('6', 'Jun'),
                                                   ('7', 'July'),
                                                   ('8', 'Aug'),
                                                   ('9', 'Sep'),
                                                   ('10', 'Oct'),
                                                   ('11', 'Nov'),
                                                   ('12', 'Dec'),
                                                   ], string="Month Pick")
    inspection_started_status_check = fields.Boolean("Inspection Started Check Bool", default = False, help = "When we change the Inspection Started From Check")
    unit_pull_out_status_check = fields.Boolean('Unit Pull Out Status Check' , default = False , help = "When Technician take the unit pull out ")
    warranty_verfication_status_check  = fields.Boolean("Warranty Verification Status Check" ,default = False , help = "Technician Change Warranty Verification Status")
    customer_need_quote_status_check = fields.Boolean(string = "Customer Need Quote Check", default = False)
    whatsapp_inspection_started_bool = fields.Boolean(string = "Whatsapp Inspection Started Bool")
    quote_created_user_id = fields.Many2one('res.users', string = "Quote Created By")


    year_pick_warranty_expiry = fields.Selection([(str(y), str(y)) for y in range(1900, 2101)], string="Year Pick")

    combine_date_warranty_expiry = fields.Date(string="Combine Warranty Date", compute="_compute_combine_date",
                                               store=True)

    # date_pick_purchase_date = fields.Selection([(str(d),str(d)) for d in range(1,32)],string = "Date Pick Purchase")
    #
    # # month_pick = fields.Selection([(str(m),str(m)) for m in range(1,13)],string = "Month Pick")
    # month_pick_purchase_date = fields.Selection([('1','Jan'),
    #                                ('2','Feb'),
    #                                ('3','Mar'),
    #                                ('4','Apr'),
    #                                ('5','May'),
    #                                ('6','Jun'),
    #                                ('7','July'),
    #                                ('8','Aug'),
    #                                ('9','Sep'),
    #                                ('10','Oct'),
    #                                ('11','Nov'),
    #                                ('12','Dec'),
    #                                ],string = "Month Pick Purchase")
    #
    # year_pick_purchase_date = fields.Selection([(str(y), str(y)) for y in range(1900,2101)],string = "Year Pick")
    #
    # combine_date_purchase_date = fields.Date(string = "Combine Purchase Date",compute = "_compute_combine_date_purchase_date" ,store = True)
    #

    @api.depends('date_pick_warranty_expiry', 'month_pick_warranty_expiry', 'year_pick_warranty_expiry')
    def _compute_combine_date(self):
        for rec in self:
            rec.combine_date_warranty_expiry = False
            if rec.date_pick_warranty_expiry and rec.month_pick_warranty_expiry and rec.year_pick_warranty_expiry:
                try:
                    rec.combine_date_warranty_expiry = date(
                        int(rec.year_pick_warranty_expiry),
                        int(rec.month_pick_warranty_expiry),
                        int(rec.date_pick_warranty_expiry)
                    )
                    if rec.combine_date_warranty_expiry:
                        rec.warranty_expiry_date = rec.combine_date_warranty_expiry
                except ValueError:
                    # Handles invalid dates like Feb 30
                    raise ValidationError(_("Invalid date selected! Please choose a valid date."))
                    rec.combine_date_warranty_expiry = False
            else:
                rec.combine_date_warranty_expiry = False

    # @api.depends('date_pick_purchase_date','month_pick_purchase_date','year_pick_purchase_date')
    # def _compute_combine_date_purchase_date(self):
    #     for rec in self:
    #         rec.combine_date_purchase_date = False
    #         if rec.date_pick_purchase_date and rec.month_pick_purchase_date and rec.year_pick_purchase_date:
    #             try:
    #                 rec.combine_date_purchase_date = date(
    #                     int(rec.year_pick_purchase_date),
    #                     int(rec.month_pick_purchase_date),
    #                     int(rec.date_pick_purchase_date)
    #                 )
    #                 if rec.combine_date_purchase_date:
    #                     rec.warranty_expiry_date  = rec.combine_date_warranty_expiry
    #             except ValueError:
    #                 # Handles invalid dates like Feb 30
    #                 raise ValidationError(_("Invalid date selected! Please choose a valid date."))
    #                 rec.combine_date_purchase_date = False
    #         else:
    #             rec.combine_date_purchase_date = False
    #

    #
    # expiry_date = fields.Char(string="Date Pick",deprecated = False)
    #
    # @api.constrains('expiry_date')
    # def _check_date_format(self):
    #     for rec in self:
    #         if rec.expiry_date:
    #             try:
    #                 datetime.strptime(rec.expiry_date, "%Y-%m-%d")  # YYYY-MM-DD format
    #             except ValueError:
    #                 raise ValidationError("Please enter the date in YYYY-MM-DD format.")

    @api.depends('length', 'width')
    def _compute_area(self):
        for rec in self:
            rec.area = rec.length * rec.width

    @api.onchange('team_id')
    def _onchange_technician_first_time(self):
        for rec in self:
            if rec.team_id:
                if not rec.second_visit_technician_bool:
                    if not rec.job_card_state_code == '117':
                        rec.technician_first_visit = rec.team_id.leader_id.name
                        rec.technician_first_visit_id = rec.team_id.leader_id.id
                if rec.second_visit_technician_bool:
                    rec.technician_second_visit = rec.team_id.leader_id.name
                    rec.technician_second_visit_id = rec.team_id.leader_id.id

    # @api.depends('team_id')
    # # @api.onchange('team_id')
    # def _compute_technician_first_visit(self):
    # # def _onchange_technician_first_time(self):
    #     for rec in self:
    #         rec.technician_first_visit = False
    #         rec.technician_second_visit = False
    #         technician_name = False
    #         if rec.team_id:
    #             if not rec.second_visit_technician_bool:
    #                 rec.technician_first_visit = rec.team_id.leader_id.name
    #                 technician_name = rec.technician_first_visit
    #             if rec.second_visit_technician_bool:
    #                 rec.technician_second_visit = rec.team_id.leader_id.name
    #                 rec.technician_first_visit = technician_name
    #

    # @api.depends('team_id', 'second_visit_technician_bool')
    # def _compute_technician_first_visit(self):
    #     for rec in self:
    #         if rec.team_id:
    #             leader_name = rec.team_id.leader_id.name or False
    #             if rec.second_visit_technician_bool:
    #                 # Only update second visit technician, keep first visit unchanged
    #                 rec.technician_second_visit = leader_name
    #             else:
    #                 # Update first visit technician when it's the first visit
    #                 rec.technician_first_visit = leader_name
    #                 rec.technician_second_visit = False
    #         else:
    #             # Clear both when no team assigned
    #             rec.technician_first_visit = False
    #             rec.technician_second_visit = False

    @api.depends('technician_first_visit_datetime', 'second_visit_technician_bool')
    def _compute_technician_first_intime(self):
        for rec in self:
            rec.technician_first_intime = False
            if rec.technician_first_visit_datetime:
                user_tz = self.env.user.tz or UTC
                user_timezone = pytz.timezone(user_tz)
                local_time = pytz.utc.localize(rec.technician_first_visit_datetime).astimezone(user_timezone)
                rec.technician_first_intime = local_time.strftime("%H:%M:%S")

    @api.depends('second_visit_technician_bool', 'technician_second_visit_datetime')
    def _compute_technician_second_intime(self):
        for rec in self:
            rec.technician_second_intime = False
            if rec.technician_second_visit_datetime and rec.second_visit_technician_bool:
                user_tz = self.env.user.tz or UTC
                user_time_zone = pytz.timezone(user_tz)
                local_time = pytz.utc.localize(rec.technician_second_visit_datetime).astimezone(user_time_zone)
                rec.technician_second_intime = local_time.strftime("%H:%M:%S")

    # @api.depends('job_state','job_card_state_code')
    # def _compute_previous_job_card_state_code(self):
    #     for rec in self:
    #         rec.previous_job_card_state_code = False
    #         job_state_code = False
    #         job_state_code = rec.job_card_state_code
    #         print("...........job_satte",job_state_code,rec.job_card_state_code)
    #
    #         if rec.job_state and rec.job_card_state_code:
    #             rec.previous_job_card_state_code = job_state_code
    #
    #

    # @api.depends('warehouse_id')
    # def _compute_warehouse_code(self):
    #     for rec in self:
    #         rec.warehouse_code = False
    #         if rec.warehouse_id:
    #             rec.warehouse_code = rec.warehouse_id.code

    def action_save(self):
        self.ensure_one()
        self.write({})  # this triggers the save
        return True

    def action_discard(self):
        self.write({'active': False})

    def action_open_js_popup(self):
        self.ensure_one()

        action = self.env.ref("project_team_assignment.action_project_task_gantt_hide_sidebar").read()[0]
        # action["target"] = "new"
        action["target"] = "current"
        action["context"] = dict(self.env.context,
                                 job_card_number=self.name,
                                 customer_name=self.customer_name or "",
                                 service_requested_datetime=self.service_requested_datetime or "",
                                 # planned_date_begin=self.planned_date_begin or '',
                                 # planned_date_end=self.planned_date_end or '',
                                 job_card_state_code=self.job_card_state_code,
                                 job_card_state=self.job_card_state,
                                 job_state=self.job_state,

                                 hide_jobcard_list=True,  # 👈 add this flag
                                 # default_date=self.planned_date_begin or fields.Date.today(),
                                 # 👇 force only date part (YYYY-MM-DD)
                                 default_date=(self.planned_date_begin or fields.Date.today()).strftime("%Y-%m-%d"),

                                 # dialog_size="large",  # optional, still used internally
                                 # dialog_class="modal-dialog modal-xl modal-dialog-centered",
                                 )

        # print(">>> Final Action Context:", action["context"])

        return action

    """def action_open_js_popup(self):
        self.ensure_one()

        print(">>> Job Card Name:", self.name)
        print(">>> Customer Name:", self.customer_name)
        print(">>> Service Requested Datetime:", self.service_requested_datetime)

        action = self.env.ref("project_team_assignment.action_project_task_gantt").read()[0]
        action["target"] = "current"
        action["context"] = dict(self.env.context,
            job_card_number=self.name,
            customer_name=self.customer_name or "",
            service_requested_datetime=self.service_requested_datetime or "",
            # dialog_size="large",  # optional, still used internally
            # dialog_class="modal-dialog modal-xl modal-dialog-centered",
        )
            print(">>> Final Action Context:", action["context"])

        return action   

        """

    # action["context"] = dict(self.env.context,
    #     job_card_number=self.name,
    #     customer_name=self.customer_name or "",
    #     service_requested_datetime=self.service_requested_datetime or "",
    # )

    @api.onchange('team_id')
    def _onchange_team_id_warehouse(self):
        for rec in self:
            if rec.team_id:
                if rec.team_id.leader_id.property_warehouse_id:
                    rec.warehouse_id = rec.team_id.leader_id.property_warehouse_id.id or None
                # if rec.user_ids.property_warehouse_id:
                #     rec.warehouse_id = rec.user_ids.property_warehouse_id.id or None
                #

    # @api.onchange("user_ids")
    # def _onchange_user_ids(self):
    #     print("..............user_ids",self.user_ids)
    #     # if self.user_ids.property_warehouse_id:
    #     #     self.warehouse_id = self.user_ids.property_warehouse_id.id or None
    #     #     # _onchange_team_id_warehouse
    #     #     # warehouse = self.user_ids[0].property_warehouse_id if hasattr(self.user_ids[0], 'property_warehouse_id') else False
    #     #     # self.warehouse_id = warehouse
    #     #
    #     # else:
    #     #     self.warehouse_id = False
    #     #     # self._compute_technician_id()

    @api.depends('warehouse_id', 'warehouse_code')
    def _compute_warehouse_name(self):
        for rec in self:
            rec.warehouse_complete_name = False
            if rec.warehouse_id and rec.warehouse_code:
                #     rec.warehouse_complete_name = '[%s]-%s'%(rec.warehouse_code,rec.warehouse_id.display_name)
                # else:
                rec.warehouse_complete_name = rec.warehouse_id.complete_name

    @api.onchange('warehouse_id')
    def _onchange_warehouse_id(self):
        for rec in self:
            if rec.warehouse_id:
                rec.warehouse_code = rec.warehouse_id.code or None
                if rec.quotation_count == 0 or rec.sale_order_state_check:
                    if rec.product_line_ids:
                        '''client asked change the warehouse.If they change then product should be cleared first and then add it.'''
                        raise ValidationError(
                            "Please remove all added parts from the list before changing the warehouse.")

                        rec.product_line_ids = [(5, 0, 0)]

    @api.constrains('warehouse_id')
    def _check_warehouse_id(self):
        for rec in self:
            if rec.quotation_count != 0 and not rec.sale_order_state_check:
                if rec.service_sale_id.warehouse_id != rec.warehouse_id:
                    # print("..............warehouse_id",rec.warehouse_id.id,rec.team_id.leader_id.property_warehouse_id)
                    # if rec.team_id.leader_id.property_warehouse_id:
                    #     if rec.warehouse_id != rec.team_id.leader_id.property_warehouse_id:
                    raise ValidationError("Don't Change the Warehouse now.Because already Quotation is provided")

    @api.depends('user_ids')
    def _compute_technician_id(self):
        """Compute technician_id based on user_ids."""
        for record in self:
            if len(record.user_ids) == 1:
                record.technician_id = record.user_ids[0]
                record.service_request_id.user_id = record.technician_id.id
                scheduled_state = self.env['project.task.type'].search(
                    [('code', '=', '102')],
                    limit=1
                )

                if scheduled_state:
                    record.job_state = scheduled_state
                    # record._onchange_team_id()
                    record.job_card_state_code = scheduled_state.code
                    record.job_card_state = scheduled_state.name
                    record.service_request_id.technician_appointment_date = record.planned_date_begin

                    record.service_request_id.service_request_state = record.job_state.name
                    record.service_request_id.service_request_state_code = record.job_state.code
                    record.service_request_id.state = record.job_state
                # # if not (record.second_visit_technician_bool and record.technician_first_intime and record.technician_first_outtime and record.technician_first_visit):
                # record.technician_first_visit_id = record.technician_id.id
                # print("....................record.technician",record.technician_first_visit_id)
                #



            else:
                record.technician_id = False

    def _inverse_technician_id(self):
        """Add technician_id to user_ids when technician_id is set."""
        for record in self:
            if record.technician_id:
                # Set user_ids to contain only the technician_id
                record.user_ids = [(5, 0, 0), (4, record.technician_id.id)]
            else:
                # Clear user_ids when technician_id is unset
                record.user_ids = [(5, 0, 0)]
        # for record in self:
        #     if record.technician_id and record.technician_id not in record.user_ids:
        #         record.user_ids = [(4, record.technician_id.id)]
        #     elif not record.technician_id and len(record.user_ids) == 1:
        #         record.user_ids = [(5, 0, 0)]
        #

    @api.constrains('technician_id', 'user_ids')
    def _check_technician_in_assignees(self):
        """Ensure technician_id is in user_ids if both are set."""
        for record in self:
            if record.technician_id and record.user_ids and record.technician_id not in record.user_ids:
                raise ValidationError("The technician must be one of the assignees.")

    @api.depends('technician_id')
    def _compute_team_id(self):
        """Compute team_id based on technician_id."""
        for record in self:
            if record.technician_id:
                team = self.env['machine.support.team'].search([('leader_id', '=', record.technician_id.id)], limit=1)
                record.team_id = team.id if team else False
            else:
                record.team_id = False

    def _search_team_id(self, operator, value):
        """Search method for team_id to allow searching based on team leader."""
        if operator not in ('=', '!='):
            raise ValueError("Unsupported operator %s for team_id search" % operator)

        # Search for teams with the given leader_id matching the value
        teams = self.env['machine.support.team'].search([('leader_id', operator, value)])
        team_ids = teams.ids if teams else [False]

        # Return domain to filter tasks based on technician_id linked to the team
        return [('technician_id', 'in', team_ids)]

    # @api.depends('user_ids')
    # def _compute_user_ids_bool(self):
    #     for rec in self:
    #         rec.user_ids_bool = False
    #         if rec.user_ids:
    #             rec.user_ids_bool = True
    #             if rec.user_ids_bool:
    #                 rec.technician_id = rec.user_ids

    @api.depends('job_state')
    def _compute_state_status(self):
        """Compute state_status and validate stock quantities for product_line_ids when job_state.code is '126'."""
        for rec in self:
            rec.state_status = False
            scheduled_state = self.env['project.task.type'].search(
                [('code', '=', '126')],
                limit=1
            )
            # print("............jobstate",rec.job_state,rec.job_state.code)

            if scheduled_state and scheduled_state.code == rec.job_state.code:
                # Check stock quantities for product_line_ids
                if rec.warehouse_id and rec.warehouse_id.lot_stock_id and rec.product_category_id:
                    location_id = rec.warehouse_id.lot_stock_id.id
                    categ_id = rec.product_category_id.id
                    validation_errors = []

                    # Collect product IDs from saved records only, excluding service products
                    product_lines = rec.product_line_ids.filtered(
                        lambda line: line.id and line.product_id.product_tmpl_id.detailed_type != 'service'
                    )  # Exclude NewId and service products

                    if product_lines:
                        product_ids = product_lines.mapped('product_id.id')
                        # Query stock quantities for all products in one go
                        # self.env.cr.execute("""
                        #         SELECT sq.product_id, COALESCE(SUM(sq.quantity), 0) as total_quantity
                        #         FROM stock_quant sq
                        #         JOIN product_product p ON sq.product_id = p.id
                        #         JOIN product_template pt ON p.product_tmpl_id = pt.id
                        #         WHERE sq.product_id IN %s
                        #         AND sq.location_id = %s
                        #         AND pt.categ_id = %s
                        #         GROUP BY sq.product_id
                        #     """, (tuple(product_ids), location_id, categ_id))
                        #
                        # stock_quantities = {(row['product_id'], row['location_id']): row['total_quantity'] for row in
                        #                     self.env.cr.dictfetchall()}
                        # for (prod_id, loc_id), quantity in stock_quantities.items():
                        #     product = self.env['product.product'].browse(prod_id)
                        #     product_name = product.display_name or product.name
                        #     _logger.debug(".....Product: %s (ID: %s), Available Quantity: %s", product_name, prod_id, quantity)
                        #

                        # Validate stock for each product line
                        for line in product_lines:
                            product = line.product_id
                            quantity = line.qty
                            product_name = line.product_id.display_name or product.name

                            product_quant_qty = 0
                            stock_quant_search = self.env['stock.quant'].search(
                                [('product_id', '=', line.product_id.id), ('location_id', '=', line.location_id.id)])
                            for quant in stock_quant_search:
                                product_quant_qty += quant.quantity

                            # stock_quantity = stock_quantities.get((product.id, location_id),0)
                            if product_quant_qty < quantity:
                                # if stock_quantity < quantity:
                                if not self.env['ir.config_parameter'].sudo().get_param(
                                        'machine_repair_management.negative_stock_allow') == 'True':
                                    validation_errors.append(
                                        f"Product '{product_name}' has insufficient stock: "
                                        f"Required {quantity}, Available {product_quant_qty}"
                                    )

                    # Raise validation error if any issues found
                    if validation_errors:
                        raise ValidationError(
                            "Stock validation failed:\n" + "\n".join(validation_errors)
                        )

                # Set state_status to True if validation passes
                rec.state_status = True
                if rec.state_status and rec.project_related_amc_bool:
                    rec.service_request_id._compute_update_contract_line()

            scheduled_state_cancel = self.env['project.task.type'].search(
                [('code', '=', '124')],
                limit=1
            )
            if scheduled_state_cancel and scheduled_state_cancel.code == rec.job_state.code:
                rec.state_status = True

            if rec.job_state.code == '124':
                rec.cancel_status_check = True
                # if rec.job_state.code == '112':
                #     cancel_status_search = self.env['cancelled.reason.wizard'].search([('job_card_id','=',self.id)],limit=1)
                #     cancel_status_search.cancellation_reason_id = self.env['cancellation.reason'].search([('code','=','007')],limit = 1).id
                #     cancel_status_search.action_confirm_reason()
                #

            if rec.job_state.code == '125':
                rec.ready_to_invoice_status_check = True

            if rec.job_state.code == '112':
                rec.cancelled_inspection_charges_bool = True

            else:
                _logger.debug("Job state code does not match '126' or scheduled_state not found for record: %s", rec)

    '''
    @api.depends('job_state')
    def _compute_state_status(self):
        for rec in self:
            rec.state_status = False
            scheduled_state = self.env['project.task.type'].search(
                                    [('code','=','126')],
                                     limit=1)
            # scheduled_state = self.env['project.task.type'].search(
            #                         [('name', '=', 'Closed'),('code','=','126')],
            #                         limit=1)

            if scheduled_state.code == rec.job_state.code:
            # if scheduled_state.name == rec.job_state.name:
                rec.state_status = True
    '''

    ''' this is currently working perfect but Commented by Vijaya Bhaskar on August 05 2025 because they don't want onchnage only save button is clicked'''

    '''this is worked commented by Vijaya Bhaskar on August 13 2025 because all the job State is doing only save button'''
    # @api.onchange('job_state')
    # def _onchange_job_card_state_status(self):
    #     for rec in self:
    #         # rec._check_planned_date_time_check()
    #         rec.job_card_state = rec.job_state.name
    #         rec.job_card_state_code = rec.job_state.code
    #         rec.service_request_id.service_request_state = rec.job_state.name
    #         rec.service_request_id.service_request_state_code = rec.job_state.code
    #         rec.service_request_id.state  = rec.job_state
    #
    #         if rec._origin and rec.job_state != rec._origin.job_state:
    #
    #             if rec.job_card_state_code =='103':
    #                 rec.technician_accepted_date = fields.Datetime.now()
    #
    #             elif rec.job_card_state_code == '104':
    #                 rec.technician_rejected_date = fields.Datetime.now()
    #
    #                 work_center = rec.technician_id.default_work_center_id
    #                 if not work_center:
    #                     _logger.warning("No work center found for technician %s on Job Card %s", rec.technician_id.name,
    #                                     rec.name)
    #                     return
    #                 # Search for finance users with the specified group and work center
    #                 finance_users = self.env['res.users'].search([
    #                     ('default_work_center_id', '=', work_center.id),
    #                     (
    #                     'groups_id', 'in', self.env.ref('machine_repair_management.group_technical_allocation_user').id)
    #                 ])
    #                 print("finance_users", finance_users)
    #                 # OdooBot as the sender
    #                 odoo_bot = self.env.ref('base.partner_root')
    #                 # Post message to each user's private Discuss channel
    #                 for user in finance_users:
    #                     if user.partner_id:
    #                         # Find or create a private channel between OdooBot and the user
    #                         channel_name = f"{odoo_bot.name}, {user.name}"
    #                         channel = self.env['discuss.channel'].search([
    #                             ('name', 'ilike', channel_name),
    #                             ('channel_type', '=', 'chat')
    #                         ], limit=1)
    #                         if not channel:
    #                             channel = self.env['discuss.channel'].create({
    #                                 'name': channel_name,
    #                                 'channel_type': 'chat',
    #                                 # 'public': 'private',
    #                                 'channel_partner_ids': [(4, user.partner_id.id)]
    #                             })
    #                         # Post the message to the private channel
    #
    #                         channel.message_post(
    #                             body=f'Technician {rec.technician_id.name} has rejected Job Card {rec.name} (Work Center: {work_center.name})',
    #                             subject='Job Card State Update',
    #                             message_type='notification',
    #                             subtype_xmlid='mail.mt_comment',
    #                             author_id=odoo_bot.id
    #                         )
    #                         print("Posted to channel for user.partner_id:", user.partner_id)
    #                 # Optional: Return a client-side notification for the current user
    #                 # return {
    #                 #     'warning': {
    #                 #         'title': 'Job Card Update',
    #                 #         'message': f'Technician {rec.technician_id.name} has rejected Job Card {rec.name}. Notifications have been sent to relevant users via Discuss.',
    #                 #     }
    #                 # }
    #
    #
    #             elif rec.job_card_state_code =='109':
    #                 rec.technician_started_date = fields.Datetime.now()
    #
    #             elif rec.job_card_state_code == '110':
    #                 rec.technician_reached_date = fields.Datetime.now()
    #
    #             elif rec.job_card_state_code =='115':
    #                 rec.job_started_date = fields.Datetime.now()
    #
    #             elif rec.job_card_state_code =='117':
    #                 # send unit receipt to whatsapp
    #                 rec._send_unit_receipt_whatsapp()
    #
    #             elif rec.job_card_state_code == '121':
    #                 rec.job_hold_date = fields.Datetime.now()
    #                 work_center = rec.technician_id.default_work_center_id
    #
    #                 # Fetch finance users from the group
    #                 group_id = self.env.ref('machine_repair_management.group_parts_user').id
    #                 finance_users = self.env['res.users'].search([('groups_id', 'in', [group_id]),('default_work_center_id', '=', work_center.id)])
    #
    #
    #                 # OdooBot as sender
    #                 odoo_bot = self.env.ref('base.partner_root')
    #
    #                 for user in finance_users:
    #                     if user.partner_id:
    #
    #                         # Create or fetch private chat channel
    #                         channel_name = f"{odoo_bot.name}, {user.name}"
    #                         channel = self.env['discuss.channel'].search([
    #                             ('name', 'ilike', channel_name),
    #                             ('channel_type', '=', 'chat')
    #                         ], limit=1)
    #                         if not channel:
    #                             channel = self.env['discuss.channel'].create({
    #                                 'name': channel_name,
    #                                 'channel_type': 'chat',
    #                                 # 'public': 'private',
    #                                 'channel_partner_ids': [(4, user.partner_id.id)]
    #                             })
    #
    #                         # Post message
    #                         message_body = f'Technician {rec.technician_id.name} has put Job Card {rec.name} on hold.'
    #                         channel.message_post(
    #                             body=message_body,
    #                             subject='Job Card State Update',
    #                             message_type='notification',
    #                             subtype_xmlid='mail.mt_comment',
    #                             author_id=odoo_bot.id
    #                         )
    #
    #                 # return {
    #                 #     'warning': {
    #                 #         'title': 'Job Card Update',
    #                 #         'message': f'Technician {rec.technician_id.name} has put Job Card {rec.name} on hold. Notifications sent.',
    #                 #     }
    #                 # }
    #             elif rec.job_card_state_code == '122':
    #
    #                 rec.job_resume_date = fields.Datetime.now()
    #                 technician_users = rec.technician_id
    #                 for line in rec.product_line_ids:
    #                     if not line.parts_reserved_bool:
    #                         raise ValidationError("Please check all the Products should be Reserved.This Product %s is not reserved" % line.product_id.display_name)
    #                     if line.on_hand_qty == 0.0:
    #                         raise ValidationError("Please Stock is not available.Please Contact Administrator")
    #
    #                 # OdooBot as sender
    #                 odoo_bot = self.env.ref('base.partner_root')
    #                 # for user in technician_users:
    #                 if technician_users.partner_id:
    #                     # Create or fetch private chat channel
    #                     channel_name = f"{odoo_bot.name}, {technician_users.name}"
    #                     channel = self.env['discuss.channel'].search([
    #                         ('name', 'ilike', channel_name),
    #                         ('channel_type', '=', 'chat')
    #                     ], limit=1)
    #                     if not channel:
    #                         channel = self.env['discuss.channel'].create({
    #                             'name': channel_name,
    #                             'channel_type': 'chat',
    #                             # 'public': 'private',
    #                             'channel_partner_ids': [(4, technician_users.partner_id.id)]
    #                         })
    #
    #                     # Post message
    #                     message_body = (
    #                         f'Parts are ready Mr. {rec.technician_id.name}. '
    #                         f'This Job Card {rec.name} has allocated parts.'
    #                     )
    #                     channel.message_post(
    #                         body=message_body,
    #                         subject='Job Card State Update',
    #                         message_type='notification',
    #                         subtype_xmlid='mail.mt_comment',
    #                         author_id=odoo_bot.id
    #                     )
    #                     # Send bus notification for sound
    #                     self.env['bus.bus']._sendone(
    #                         technician_users.partner_id,
    #                         'job_card_alert',
    #                         {
    #                             'type': 'job_card_alert',
    #                             'title': 'Job Card Update',
    #                             'message': message_body,
    #                             'sound': True
    #                         }
    #                     )
    #
    #                     # return {
    #                     #     'warning': {
    #                     #         'title': 'Job Card Update',
    #                     #         'message': message_body,
    #                     #     }
    #                     # }
    #
    #             elif rec.job_card_state_code =='123':
    #                 rec.job_resume_date = fields.Datetime.now()
    #
    #             elif rec.job_card_state_code == '125':
    #                 rec.closed_datetime = fields.Datetime.now()
    #
    #                 work_center = rec.technician_id.default_work_center_id
    #
    #                 finance_users = self.env['res.users'].search([
    #                     ('default_work_center_id', '=', work_center.id),
    #                     (
    #                         'groups_id', 'in',
    #                         self.env.ref('machine_repair_management.group_technical_allocation_user').id)
    #                 ])
    #
    #                 # if finance_users and rec.technician_id.partner_id:
    #                 #     technician_user = rec.technician_id
    #                 #     technician_partner = technician_user.partner_id
    #                 odoo_bot = self.env.ref('base.partner_root')
    #
    #                 # Combine partner IDs into a single flat list
    #                 for user in finance_users:
    #                     if user.partner_id:
    #                         # Find or create a private channel between OdooBot and the user
    #                         channel_name = f"{odoo_bot.name}, {user.name}"
    #                         channel = self.env['discuss.channel'].search([
    #                             ('name', 'ilike', channel_name),
    #                             ('channel_type', '=', 'chat')
    #                         ], limit=1)
    #                         if not channel:
    #                             channel = self.env['discuss.channel'].create({
    #                                 'name': channel_name,
    #                                 'channel_type': 'chat',
    #                                 # 'public': 'private',
    #                                 'channel_partner_ids': [(4, user.partner_id.id)]
    #                             })
    #
    #
    #                         # Post the message
    #                         message_body = f'Job Card {rec.name} has been completed and is ready to be invoiced.'
    #                         channel.message_post(
    #                             body=message_body,
    #                             subject='Job Card State Update',
    #                             message_type='notification',
    #                             subtype_xmlid='mail.mt_comment',
    #                             author_id=odoo_bot.id,
    #                         )
    #
    #                         _logger.info("Marked as ready to invoice for user  in channel %s", channel.name)
    #
    #
    #             # If the User selected the Parts Ready Job state then check all the parts should be ticked in the product consume parts/service by Vijaya Bhaskar on June-30-2025
    #             elif rec.job_card_state_code in ('122','126'):
    #                 if rec.product_line_ids:
    #                     for line in rec.product_line_ids:
    #                         if line.product_id:
    #                             if not line.parts_reserved_bool:
    #                                 raise ValidationError("Please check all the Products should be Reserved.This Product %s is not reserved" % line.product_id.display_name)
    #                         if line.on_hand_qty == 0.0:
    #                             raise ValidationError("Please Stock is not available.Please Contact Administrator")
    #
    #
    #                 if not rec.product_line_ids:
    #                     raise ValidationError("Please give any one of the Product in the product consume Part/services")
    #
    #
    #
    #             elif rec.job_card_state_code == '102':
    #                 if not rec.team_id :
    #                     raise ValidationError("Please enter Team Leader name in the Job card")
    #
    #                 if not rec.technician_id:
    #
    #                     raise ValidationError("Please Enter Technician Name ")
    #
    #                 technician_users = rec.technician_id
    #                 _logger.info("Supervisor users to notify: %s", technician_users)
    #                 # OdooBot as sender
    #                 odoo_bot = self.env.ref('base.partner_root')
    #                 # for user in technician_users:
    #                 if technician_users.partner_id:
    #                     # Create or fetch private chat channel
    #                     channel_name = f"{odoo_bot.name}, {technician_users.name}"
    #                     channel = self.env['discuss.channel'].search([
    #                         ('name', 'ilike', channel_name),
    #                         ('channel_type', '=', 'chat')
    #                     ], limit=1)
    #                     if not channel:
    #                         channel = self.env['discuss.channel'].create({
    #                             'name': channel_name,
    #                             'channel_type': 'chat',
    #                             # 'public': 'private',
    #                             'channel_partner_ids': [(4, technician_users.partner_id.id)]
    #                         })
    #
    #                     # Post message
    #                     message_body = (
    #                         f'Job Card {rec.name} has been assigned to Mr. {rec.technician_id.name}.'
    #                     )
    #                     channel.message_post(
    #                         body=message_body,
    #                         subject='Job Card State Update',
    #                         message_type='notification',
    #                         subtype_xmlid='mail.mt_comment',
    #                         author_id=odoo_bot.id
    #                     )
    #                     _logger.info("Posted message to user %s via channel %s", technician_users.name, channel.name)
    #
    #                     # return {
    #                     #     'warning': {
    #                     #         'title': 'Job Card Update',
    #                     #         'message': message_body,
    #                     #     }
    #                     # }
    #
    #                 # self._send_whatsapp_scheduled_message()
    #
    #             elif rec.job_card_state  not in  ('101','102'):
    #
    #                 if not rec.team_id :
    #
    #                     raise ValidationError("Please give the Team Leader Name")
    #
    #                 if not rec.technician_id:
    #
    #                     raise ValidationError("Please Enter Technician Name ")
    #
    #                 # if not rec.service_requested_datetime:
    #                 #
    #                 #     raise ValidationError("Please Enter  Requested Date & Time")
    #
    #                 #Commented on Jun - 7 -2025 for replace appointment datetime with planned_date_begin for scheduling
    #                 # if not rec.appointment_datetime:
    #                 #     raise ValidationError("Please Enter Appointment Date & Time")
    #                 if not rec.planned_date_begin:
    #
    #                     raise ValidationError("Please Enter Appt Start Date & Time")
    #
    #                 if rec.job_card_state_code == '126':
    #                     if not rec.closed_datetime:
    #                         raise ValidationError("Please Enter Closed Date time.")
    #                     elif rec.quotation_count == 0:
    #                     # if not rec.sale_id:
    #                         if rec.product_line_ids:
    #                             for line in rec.product_line_ids:
    #                                 if not line.under_warranty_bool:
    #                                     raise ValidationError("Complete your quotation first, then close the job card")
    #
    #                     elif self.product_line_ids:
    #                         if self.inspection_charges_bool and self.inspection_charges_amount > 0:
    #                             if not any(line.product_id and line.product_id.service_type_bool for line in self.product_line_ids):
    #                                 raise ValidationError("Please enter service charge amount in the product line")
    #
    #

    # if self.env.user.has_group('machine_repair_management.group_technical_allocation_user'):
    #     if not rec.supervisor_comments:
    #         raise ValidationError("Please give the supervisor comments for this Job card")

    ''' send Email to parts user because the code is  On Hold Spare Parts  code is added on Oct-03 2025'''

    def _send_email_for_parts_user(self):

        work_center_group = False

        work_center_group = self.work_center_group_id

        work_center_search = self.env['work.center.location'].search(
            [('work_center_group_id', '=', work_center_group.id)])

        user_search = self.env['res.users'].search([
            ('groups_id', 'in', self.env.ref('machine_repair_management.group_parts_user').id),
            ('default_work_center_id', 'in', work_center_search.ids)

        ])
        if not user_search:
            return
        for user in user_search:
            # if user.has_group('machine_repair_management.group_parts_user'):

            subject = f"Spare Parts Required – Service Request No. {self.name} "
            body_html = f"""
            <p style="color:#0000FF;font-size:20px">Dear {user.name} </p>
             <p style="color:#0000FF;font-size:20px">
                Please note that Service Request No.{self.name} requires spare parts to complete the repair.
             </p>
             <p style="color:#0000FF;font-size:20px">
               Kindly check the availability of the required parts from your account in Cielo Cloud.
               <br/>
               Thank you for your support.
             </p>

            <br/>
            <b style="color:#0000FF;font-size:20px">Best Regards</b><br/>
            <b style="color:#0000FF;font-size:20px">Maintenance Dept</b><br/>
             <b style="color:#0000FF;font-size:20px">HH-Shaker</b>

            """

            self.env['mail.mail'].create({
                'subject': subject,
                'body_html': body_html,
                'email_from': self.env.user.email,
                'email_to': user.login,
                # 'email_cc' :

            })

            if self.service_request_id:
                self.service_request_id.message_post(
                    body=f"Parts requirement email sent to {user.name}",
                    subject=subject,
                    message_type='comment',
                    subtype_xmlid='mail.mt_comment'
                )

    ''' Send Whatsapp for Parts User is added on Oct 03-2025'''
    '''            
    def _send_whatsapp_for_parts_user(self):


        # if not self.whatsapp_send_bool:
        #     _logger.info("❌ No WhatsApp set in res Config Settings")
        #     return False

        if not self.env['ir.config_parameter'].sudo().get_param('machine_repair_management.whatsapp_send_bool') == 'True':
            _logger.info("❌ No WhatsApp set in res Config Settings")
            return False


        whatsapp_opt_in  = False
        message = False

        work_center_group = False

        work_center_group = self.work_center_group_id

        work_center_search  = self.env['work.center.location'].search([('work_center_group_id','=',work_center_group.id)])

        user_search = self.env['res.users'].search([
            ('groups_id','in', self.env.ref('machine_repair_management.group_parts_user').id),
            ('default_work_center_id','in',work_center_search.ids)

            ])
        if not user_search:
            return

        # user_search = self.env['res.users'].search([
        #     ('groups_id','in', self.env.ref('machine_repair_management.group_parts_user').id)
        #
        #     ])
        for user in user_search:
            scheduled_state = self.env['project.task.type'].search(
                            [('code', '=', '121')],
                            limit=1
                        )
            if scheduled_state:
                if scheduled_state.code == self.job_card_state_code:
                    if scheduled_state.whatsapp_bool:
                        whatsapp_opt_in = True
                        arabic = scheduled_state.whatsapp_ar_template
                        english = scheduled_state.whatsapp_en_template
                        english = english.replace("{{customer name}}", self.customer_name).replace("{{Job Card No.}}", self.name)
                        arabic = arabic.replace("{{customer name}}", self.customer_name).replace("{{Job Card No.}}", self.name)
                        separator = "\n" + "-" * 50 + "\n"
                        message = arabic + separator + english

            phone_number = False
            phone_number = user.partner_id.phone 
            country_code = user.partner_id.country_id.phone_code
            if phone_number:
                phone_number = phone_number.replace('+', '').replace("", "")
                phone_number = f"{country_code}{phone_number}"

            whatsapp_opt = user.partner_id.x_whatsapp_opt_in
            if not whatsapp_opt:
                _logger.info("❌ No WhatsApp opt-in for Parts user %s", self.customer_name)
                return False


            if not whatsapp_opt_in:
                _logger.info("❌ No WhatsApp opt-in for customer for job card customer %s", self.customer_name)
                return False
            if not phone_number:
                _logger.info("❌ No mobile number found for customer %s", self.customer_name)
                return False               


            whatsapp_phone_number_id = f"{self.env['ir.config_parameter'].sudo().get_param('whatsapp_sale_order_notify.whatsapp_phone_number_id')}"

            base_url = f'https://graph.facebook.com/v18.0/{whatsapp_phone_number_id}'

            access_token = f"{self.env['ir.config_parameter'].sudo().get_param('whatsapp_sale_order_notify.whatsapp_access_token')}"

            if not access_token:
                _logger.error("❌ No WhatsApp access token configured")
                return False
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'

            }
            template_url = f"{base_url}/messages"

            # message = f"Dear {user.name},\n Some Parts of the Product is not available.Please Check for the Job Card Number {self.name}.\n\n Thank You.\n Service Team"

            template_payload = {

                'messaging_product':"whatsapp",
                'to':phone_number,
                "type":"text",
                "text":{
                    'body': message,
                    }

                }
            try:
                response = requests.post(template_url, headers=headers, json=template_payload)
                response.raise_for_status()  # Raise an exception for HTTP errors

                # Use message_notify instead of message_post for user notifications
                self.service_request_id.message_post(body=_("WhatsApp Job card  message sent successfully to the Parts User"))
                return True

            except requests.exceptions.RequestException as e:
                _logger.error("❌ WhatsApp message failed: %s", str(e))
                # Optionally, notify the user or log the error in the chatter
                self.service_request_id.message_post(
                    body=_("WhatsApp scheduled message sent successfully to %s") % self.partner_id.name,
                    message_type='notification',

                )
                return False
    '''

    '''code added on Nov 14 -2025 send whatsapp to customer for on hold spare parts'''

    def _send_whatsapp_for_parts_user(self):

        # if not self.whatsapp_send_bool:
        #     _logger.info("❌ No WhatsApp set in res Config Settings")
        #     return False

        if not self.env['ir.config_parameter'].sudo().get_param(
                'machine_repair_management.whatsapp_send_bool') == 'True':
            _logger.info("❌ No WhatsApp set in res Config Settings")
            return False

        whatsapp_opt_in = False
        message = False

        scheduled_state = self.env['project.task.type'].search(
            [('code', '=', '121')],
            limit=1
        )
        if scheduled_state:
            if scheduled_state.code == self.job_card_state_code:
                if scheduled_state.whatsapp_bool:
                    whatsapp_opt_in = True
                    arabic = scheduled_state.whatsapp_ar_template
                    english = scheduled_state.whatsapp_en_template
                    english = english.replace("{{customer name}}", self.customer_name).replace("{{Job Card No.}}",
                                                                                               self.name)
                    arabic = arabic.replace("{{customer name}}", self.customer_name).replace("{{Job Card No.}}",
                                                                                             self.name)
                    separator = "\n" + "-" * 50 + "\n"
                    message = arabic + separator + english

        phone_number = False
        phone_number = self.phone
        country_code = self.country_id.phone_code
        if phone_number:
            phone_number = phone_number.replace('+', '').replace("", "")
            phone_number = f"{country_code}{phone_number}"

        # whatsapp_opt = user.partner_id.x_whatsapp_opt_in
        if not self.whatsapp_opt_in:
            _logger.info("❌ No WhatsApp opt-in for Customer %s", self.customer_name)
            return False

        if not whatsapp_opt_in:
            _logger.info("❌ No WhatsApp opt-in for customer for job card customer %s", self.customer_name)
            return False
        if not phone_number:
            _logger.info("❌ No mobile number found for customer %s", self.customer_name)
            return False

        whatsapp_phone_number_id = f"{self.env['ir.config_parameter'].sudo().get_param('whatsapp_sale_order_notify.whatsapp_phone_number_id')}"

        base_url = f'https://graph.facebook.com/v18.0/{whatsapp_phone_number_id}'

        access_token = f"{self.env['ir.config_parameter'].sudo().get_param('whatsapp_sale_order_notify.whatsapp_access_token')}"

        if not access_token:
            _logger.error("❌ No WhatsApp access token configured")
            return False
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'

        }
        template_url = f"{base_url}/messages"

        # message = f"Dear {user.name},\n Some Parts of the Product is not available.Please Check for the Job Card Number {self.name}.\n\n Thank You.\n Service Team"

        template_payload = {

            'messaging_product': "whatsapp",
            'to': phone_number,
            "type": "text",
            "text": {
                'body': message,
            }

        }
        try:
            response = requests.post(template_url, headers=headers, json=template_payload)
            response.raise_for_status()  # Raise an exception for HTTP errors

            # Use message_notify instead of message_post for user notifications
            self.service_request_id.message_post(
                body=_("WhatsApp On hold Spare Parts message sent successfully to the Parts User"))
            return True

        except requests.exceptions.RequestException as e:
            _logger.error("❌ WhatsApp message failed: %s", str(e))
            # Optionally, notify the user or log the error in the chatter
            self.service_request_id.message_post(
                body=_("WhatsApp OnHold Spare Parts message sent successfully to %s") % self.customer_name,
                message_type='notification',

            )
            return False

    ''' send Email to Supervisor user for parts Ready code is added on Oct-09 2025'''

    def _send_email_for_supervisor_user(self):

        work_center_group = False

        work_center_group = self.work_center_group_id

        work_center_search = self.env['work.center.location'].search(
            [('work_center_group_id', '=', work_center_group.id)])

        supervisor_user_search = self.env['res.users'].search([
            ('groups_id', 'in', self.env.ref('machine_repair_management.group_technical_allocation_user').id),
            ('default_work_center_id', 'in', work_center_search.ids)

        ])
        if not supervisor_user_search:
            return
        for user in supervisor_user_search:

            subject = f"Parts are Ready for the Job Card :{self.name} "
            body_html = f"""
            <p style = "color:#0000FF;font-size:20px">Dear {user.name} </p>
             <p style = "color:#0000FF;font-size:20px">
                  Products are added for the Job Card No.{self.name}.Please Check that
             </p>


             <br/>
            <b style = "color:#0000FF;font-size:20px">Best Regards</b><br/>
            <b style = "color:#0000FF;font-size:20px">Maintenance Dept</b><br/>
             <b style = "color:#0000FF;font-size:20px">HH-Shaker</b>

            """

            self.env['mail.mail'].create({
                'subject': subject,
                'body_html': body_html,
                'email_from': self.env.user.email,
                'email_to': user.login,

            })

            if self.service_request_id:
                self.service_request_id.message_post(
                    body=f"Parts ready email sent to {user.name}",
                    subject=subject,
                    message_type='comment',
                    subtype_xmlid='mail.mt_comment'
                )

    ''' Send Whatsapp for Supervisor User is added on Oct 09-2025'''

    def _send_whatsapp_for_supervisor_user(self):

        # if not self.whatsapp_send_bool:
        #     _logger.info("❌ No WhatsApp set in res Config Settings")
        #     return False
        if not self.env['ir.config_parameter'].sudo().get_param(
                'machine_repair_management.whatsapp_send_bool') == 'True':
            _logger.info("❌ No WhatsApp set in res Config Settings")
            return False

        whatsapp_opt_in = False
        message = False

        work_center_group = False

        work_center_group = self.work_center_group_id

        work_center_search = self.env['work.center.location'].search(
            [('work_center_group_id', '=', work_center_group.id)])

        supervisor_user_search = self.env['res.users'].search([
            ('groups_id', 'in', self.env.ref('machine_repair_management.group_technical_allocation_user').id),
            ('default_work_center_id', 'in', work_center_search.ids)

        ])
        if not supervisor_user_search:
            return

        for user in supervisor_user_search:

            scheduled_state = self.env['project.task.type'].search(
                [('code', '=', '122')],
                limit=1
            )
            if scheduled_state:
                if scheduled_state.code == self.job_card_state_code:
                    if scheduled_state.whatsapp_bool:
                        whatsapp_opt_in = True
                        arabic = scheduled_state.whatsapp_ar_template
                        english = scheduled_state.whatsapp_en_template
                        separator = "\n" + "-" * 50 + "\n"
                        message = arabic + separator + english

            phone_number = False
            phone_number = user.partner_id.phone
            country_code = user.partner_id.country_id.phone_code
            if phone_number:
                phone_number = phone_number.replace('+', '').replace("", "")
                phone_number = f"{country_code}{phone_number}"

            whatsapp_opt = user.partner_id.x_whatsapp_opt_in
            if not whatsapp_opt:
                _logger.info("❌ No WhatsApp opt-in for Supervisor user %s", self.customer_name)
                return False

            if not whatsapp_opt_in:
                _logger.info("❌ No WhatsApp opt-in for customer for job card customer %s", self.customer_name)
                return False
            if not phone_number:
                _logger.info("❌ No mobile number found for customer %s", self.customer_name)
                return False

            whatsapp_phone_number_id = f"{self.env['ir.config_parameter'].sudo().get_param('whatsapp_sale_order_notify.whatsapp_phone_number_id')}"

            base_url = f'https://graph.facebook.com/v18.0/{whatsapp_phone_number_id}'

            access_token = f"{self.env['ir.config_parameter'].sudo().get_param('whatsapp_sale_order_notify.whatsapp_access_token')}"

            if not access_token:
                _logger.error("❌ No WhatsApp access token configured")
                return False
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'

            }
            template_url = f"{base_url}/messages"

            # message = f"Dear {user.name},\n Some Parts of the Product is not available.Please Check for the Job Card Number {self.name}.\n\n Thank You.\n Service Team"

            template_payload = {

                'messaging_product': "whatsapp",
                'to': phone_number,
                "type": "text",
                "text": {
                    'body': message,
                }

            }
            try:
                response = requests.post(template_url, headers=headers, json=template_payload)
                response.raise_for_status()  # Raise an exception for HTTP errors

                # Use message_notify instead of message_post for user notifications
                self.service_request_id.message_post(
                    body=_("WhatsApp Job card  message sent successfully to Supervisor User"))
                return True

            except requests.exceptions.RequestException as e:
                _logger.error("❌ WhatsApp message failed: %s", str(e))
                # Optionally, notify the user or log the error in the chatter
                self.service_request_id.message_post(
                    body=_("WhatsApp scheduled message sent successfully to %s") % self.partner_id.name,
                    message_type='notification',

                )
                return False

    @api.depends('customer_name', 'job_card_state_code')
    # @api.depends('team_id','planned_date_begin','job_card_state_code')
    def _compute_whatsapp_scheduled_message_sent_bool(self):
        for rec in self:
            rec.whatsapp_scheduled_message_sent_bool = False
            if rec.job_card_state_code == '102':
                if rec.team_id and rec.planned_date_begin:
                    rec.whatsapp_scheduled_message_sent_bool = True
                    if rec.whatsapp_scheduled_message_sent_bool:
                        rec._send_whatsapp_scheduled_message()
                        # rec._send_whatsapp_scheduled_technician_message()
                        rec.whatsapp_scheduled_message_sent_bool = False

    '''send whatsapp to customer for allocated job card'''

    def _send_whatsapp_scheduled_message(self):

        # if not self.whatsapp_send_bool:
        #     _logger.info("❌ No WhatsApp set in res Config Settings")
        #     return False

        if not self.env['ir.config_parameter'].sudo().get_param(
                'machine_repair_management.whatsapp_send_bool') == 'True':
            _logger.info("❌ No WhatsApp set in res Config Settings")
            return False

        whatsapp_opt_in = False
        whatsapp_opt = False
        message = False

        scheduled_state = self.env['project.task.type'].search(
            [('code', '=', '102')],
            limit=1
        )

        slots = False
        english_slot = False
        arabic_slot = False

        if self.planned_date_begin:

            if (self.planned_date_begin.hour + 3) < 12:

                english_slot = f"{self.planned_date_begin.strftime('%d-%m-%Y')} in the Morning"
                arabic_slot = f"{self.planned_date_begin.strftime('%d-%m-%Y')}  في الفتره الصباحية"
                # slots = f"{self.planned_date_begin.strftime('%d-%m-%Y')} on morning :  الصباحيه (9:00 AM – 12:00 PM)"

            else:
                english_slot = f"{self.planned_date_begin.strftime('%d-%m-%Y')} in the Evening"
                arabic_slot = f"{self.planned_date_begin.strftime('%d-%m-%Y')}   في الفتره المسائيه"

                # slots = f"{self.planned_date_begin.strftime('%d-%m-%Y')} on Evening : المسائيه (1:00 PM – 5:00 PM)"

        if scheduled_state:
            if scheduled_state.code == self.job_card_state_code:
                if scheduled_state.whatsapp_bool:
                    whatsapp_opt = True
                    arabic = scheduled_state.whatsapp_ar_template
                    english = scheduled_state.whatsapp_en_template
                    english_format = english.replace(
                        "{{customer name}}", self.customer_name or ''
                    ).replace("{{Service request No}}", str(self.name)).replace("{{date}}", english_slot).replace(
                        "{{technician name}}", self.team_id.name)
                    arabic_format = arabic.replace("{{customer name}}", self.customer_name or '').replace(
                        "{{Service request No}}", str(self.name)).replace("{{date}}", arabic_slot).replace(
                        "{{technician name}}", self.team_id.name)
                    # english = english.replace("Dear Customer",self.customer_name).replace("Midea",self.product_category_id.name)
                    separator = "\n" + "-" * 50 + "\n"
                    message = arabic_format + separator + english_format

        phone_number = self.phone

        whatsapp_opt_in = self.whatsapp_opt_in
        country_code = self.country_id.phone_code
        if not whatsapp_opt:
            _logger.info("❌ No WhatsApp opt-in Project Task Stages %s", self.customer_name)
            return False

        if not whatsapp_opt_in:
            _logger.info("❌ No WhatsApp opt-in for customer for job card customer %s", self.customer_name)
            return False
        if not phone_number:
            _logger.info("❌ No mobile number found for customer %s", self.customer_name)
            return False
        phone_number = phone_number.replace('+', ' ').replace('', '')
        phone_number = f"{country_code}{phone_number}"

        whatsapp_phone_number_id = f"{self.env['ir.config_parameter'].sudo().get_param('whatsapp_sale_order_notify.whatsapp_phone_number_id')}"

        base_url = f'https://graph.facebook.com/v18.0/{whatsapp_phone_number_id}'

        access_token = f"{self.env['ir.config_parameter'].sudo().get_param('whatsapp_sale_order_notify.whatsapp_access_token')}"

        if not access_token:
            _logger.error("❌ No WhatsApp access token configured")
            return False
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'

        }
        template_url = f"{base_url}/messages"

        # message = False

        # slots = False

        # if self.planned_date_begin:
        #
        #     if self.planned_date_begin.hour < 12:
        #
        #         slots = "Morning Slot"
        #     else:
        #         slots = "Afternoon Slot"
        #

        # message = f"Dear {self.customer_name},\n  Our technician will visit your location by {self.planned_date_begin.strftime('%d-%m-%Y %H:%M:%S')}.His name is {self.team_id.name} and his Contact number is {self.team_id.leader_id.partner_id.mobile}.\n Should you wish to change the schedule, kindly contact our team. Otherwise kindly share the WhatsApp location to our technician with Job order number.\n\n Thank You.\n Service Team"

        # message = f"Dear {self.customer_name},\n  Our technician will visit your location by {slots}.His name is {self.team_id.name} and his Contact number is {self.team_id.leader_id.partner_id.mobile}.\n Should you wish to change the schedule, kindly contact our team. Otherwise kindly share the WhatsApp location to our technician with Job order number.\n\n Thank You.\n Service Team"
        # else:
        #
        #     message = f"Dear {self.customer_name},\n  Our technician will visit your location by {self.planned_date_begin.strftime('%d-%m-%Y %H:%M:%S')}.His name is {self.team_id.name} and his Contact number is{}.\n Should you wish to change the schedule, kindly contact our team. Otherwise kindly share the WhatsApp location to our technician with Job order number.\n\n Thank You.\n Service Team"
        #
        template_payload = {

            'messaging_product': "whatsapp",
            'to': phone_number,
            "type": "text",
            "text": {
                'body': message,
            }

        }
        try:
            response = requests.post(template_url, headers=headers, json=template_payload)
            response.raise_for_status()  # Raise an exception for HTTP errors

            # Use message_notify instead of message_post for user notifications
            self.service_request_id.message_post(
                body=_("WhatsApp Job card %s scheduled message sent successfully to the customer") % self.name)
            return True

        except requests.exceptions.RequestException as e:
            _logger.error("❌ WhatsApp message failed: %s", str(e))
            # Optionally, notify the user or log the error in the chatter
            self.service_request_id.message_post(
                body=_("WhatsApp scheduled message sent successfully to %s") % self.partner_id.name,
                message_type='notification',

            )
            return False
        # self._send_whatsapp_scheduled_technician_message()

    ''' send whatsapp to technician for allocated job card'''

    def _send_whatsapp_scheduled_technician_message(self):

        # if not self.whatsapp_send_bool:
        #     _logger.info("❌ No WhatsApp set in res Config Settings")
        #     return False

        if not self.env['ir.config_parameter'].sudo().get_param(
                'machine_repair_management.whatsapp_send_bool') == 'True':
            _logger.info("❌ No WhatsApp set in res Config Settings")
            return False
        phone_number = False
        whatsapp_opt_in = False
        country_code = False
        phone_number = self.technician_id.partner_id.mobile
        whatsapp_opt_in = self.technician_id.partner_id.x_whatsapp_opt_in
        country_code = self.technician_id.partner_id.country_id.phone_code
        if not whatsapp_opt_in:
            _logger.info("❌ No WhatsApp opt-in for Technician %s", self.technician_id.partner_id.name)
            return False
        if not phone_number:
            _logger.info("❌ No mobile number found for Technician %s", self.technician_id.partner_id.name)
            return False
        phone_number = phone_number.replace('+', ' ').replace('', '')
        phone_number = f"{country_code}{phone_number}"

        whatsapp_phone_number_id = f"{self.env['ir.config_parameter'].sudo().get_param('whatsapp_sale_order_notify.whatsapp_phone_number_id')}"

        base_url = f'https://graph.facebook.com/v18.0/{whatsapp_phone_number_id}'

        access_token = f"{self.env['ir.config_parameter'].sudo().get_param('whatsapp_sale_order_notify.whatsapp_access_token')}"

        if not access_token:
            _logger.error("❌ No WhatsApp access token configured")
            return False
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'

        }
        template_url = f"{base_url}/messages"

        message = False

        if self.planned_date_begin:
            message = f"Dear {self.team_id.name},\n  You are allocated for Job number {self.name} at {self.planned_date_begin.strftime('%d-%m-%Y %H:%M:%S')}.\n\n Thank You.\n Service Team"
        template_payload = {

            'messaging_product': "whatsapp",
            'to': phone_number,
            "type": "text",
            "text": {
                'body': message,
            }

        }

        # template_payload = {
        #         "messaging_product": "whatsapp",
        #         "to": phone_number,
        #         "type": "template",
        #         "template": {
        #             "name": "technician_scheduled",
        #             "namespace": "395abab3_2db3_443f_b5f4_581f6281ae2c",
        #             "language": {
        #                 "code": "en"  # Adjust language code as needed
        #             },
        #             "components": [
        #                 {
        #                     "type": "body",
        #                     "parameters": [
        #                         {"type": "text", "text": self.team_id.name},  # {{1}} Customer name
        #                         {"type": "text", "text": self.name},  # {{2}} Scheduled date
        #                         {"type": "text", "text": self.planned_date_begin.strftime('%d-%m-%Y %H:%M:%S')},  # {{3}} Technician team name
        #
        #                     ]
        #                 }
        #                 ]
        #
        #             }
        #         }
        #

        try:
            response = requests.post(template_url, headers=headers, json=template_payload)
            response.raise_for_status()  # Raise an exception for HTTP errors
            # Use message_notify instead of message_post for user notifications
            self.service_request_id.message_post(
                body=_("WhatsApp Job card %s scheduled message sent successfully to Technician") % self.name)
            return True

        except requests.exceptions.RequestException as e:
            _logger.error("❌ WhatsApp message failed: %s", str(e))
            # Optionally, notify the user or log the error in the chatter
            self.service_request_id.message_post(
                body=_("WhatsApp scheduled message sent successfully to %s") % self.partner_id.name,
                message_type='notification',

            )
            return False

    ''' Whatsapp Send to customer when they failed to attend the call added on Sep 4 2025'''

    def _send_failed_to_attend_call_status_whatsapp(self):
        # if not self.whatsapp_send_bool:
        #     _logger.info("❌ No WhatsApp set in res Config Settings")
        #     return False
        if not self.env['ir.config_parameter'].sudo().get_param(
                'machine_repair_management.whatsapp_send_bool') == 'True':
            _logger.info("❌ No WhatsApp set in res Config Settings")
            return False

        whatsapp_opt_in = False
        message = False

        scheduled_state = self.env['project.task.type'].search(
            [('code', '=', '105')],
            limit=1
        )
        if scheduled_state:
            if scheduled_state.code == self.job_card_state_code:
                if scheduled_state.whatsapp_bool:
                    whatsapp_opt_in = True
                    arabic = scheduled_state.whatsapp_ar_template
                    english = scheduled_state.whatsapp_en_template
                    english = english.replace("Dear Customer", self.customer_name).replace("Midea",
                                                                                           self.product_category_id.name)
                    separator = "\n" + "-" * 50 + "\n"
                    message = arabic + separator + english

        phone_number = self.phone
        # whatsapp_opt_in = self.whatsapp_opt_in
        country_code = self.country_id.phone_code
        if not whatsapp_opt_in:
            _logger.info("❌ No WhatsApp opt-in for customer for job card customer %s", self.customer_name)
            return False
        if not self.whatsapp_opt_in:
            _logger.info("❌ No WhatsApp opt-in for Customer %s", self.customer_name)
            return False
        if not phone_number:
            _logger.info("❌ No mobile number found for customer %s", self.customer_name)
            return False
        phone_number = phone_number.replace('+', ' ').replace('', '')
        phone_number = f"{country_code}{phone_number}"

        whatsapp_phone_number_id = f"{self.env['ir.config_parameter'].sudo().get_param('whatsapp_sale_order_notify.whatsapp_phone_number_id')}"

        base_url = f'https://graph.facebook.com/v18.0/{whatsapp_phone_number_id}'

        access_token = f"{self.env['ir.config_parameter'].sudo().get_param('whatsapp_sale_order_notify.whatsapp_access_token')}"

        if not access_token:
            _logger.error("❌ No WhatsApp access token configured")
            return False
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'

        }
        template_url = f"{base_url}/messages"

        template_payload = {

            'messaging_product': "whatsapp",
            'to': phone_number,
            "type": "text",
            "text": {
                'body': message,
            }

        }
        try:
            response = requests.post(template_url, headers=headers, json=template_payload)
            response.raise_for_status()  # Raise an exception for HTTP errors

            self.service_request_id.message_post(body=_(
                "WhatsApp Job card %s Failed to attend call message sent successfully to the customer") % self.name)
            return True

        except requests.exceptions.RequestException as e:
            _logger.error("❌ WhatsApp message failed: %s", str(e))
            # Optionally, notify the user or log the error in the chatter
            self.service_request_id.message_post(
                body=_("WhatsApp Failed message sent successfully to %s") % self.partner_id.name,
                message_type='notification',

            )
            return False

    '''code is added on Nov-07-2025 for cancelled inspection charges by cst'''

    def _send_whatsapp_for_cancelled_insp_charges_by_cst(self):

        if not self.env['ir.config_parameter'].sudo().get_param(
                'machine_repair_management.whatsapp_send_bool') == 'True':
            _logger.info("❌ No WhatsApp set in res Config Settings")
            return False

        whatsapp_opt_in = False
        message = False

        scheduled_state = self.env['project.task.type'].search(
            [('code', '=', '112')],
            limit=1
        )
        if scheduled_state:
            if scheduled_state.code == self.job_card_state_code:
                if scheduled_state.whatsapp_bool:
                    whatsapp_opt_in = True
                    arabic = scheduled_state.whatsapp_ar_template
                    english = scheduled_state.whatsapp_en_template
                    english = english.replace("{{customer name}}", self.customer_name).replace("{{Service request No}}",
                                                                                               self.name)
                    arabic = arabic.replace("{{customer name}}", self.customer_name).replace("{{Service request No}}",
                                                                                             self.name)
                    separator = "\n" + "-" * 50 + "\n"
                    message = arabic + separator + english

        phone_number = self.phone
        # whatsapp_opt_in = self.whatsapp_opt_in
        country_code = self.country_id.phone_code
        if not whatsapp_opt_in:
            _logger.info("❌ No WhatsApp opt-in for customer for job card customer %s", self.customer_name)
            return False
        if not self.whatsapp_opt_in:
            _logger.info("❌ No WhatsApp opt-in for Customer %s", self.customer_name)
            return False
        if not phone_number:
            _logger.info("❌ No mobile number found for customer %s", self.customer_name)
            return False
        phone_number = phone_number.replace('+', ' ').replace('', '')
        phone_number = f"{country_code}{phone_number}"

        whatsapp_phone_number_id = f"{self.env['ir.config_parameter'].sudo().get_param('whatsapp_sale_order_notify.whatsapp_phone_number_id')}"

        base_url = f'https://graph.facebook.com/v18.0/{whatsapp_phone_number_id}'

        access_token = f"{self.env['ir.config_parameter'].sudo().get_param('whatsapp_sale_order_notify.whatsapp_access_token')}"

        if not access_token:
            _logger.error("❌ No WhatsApp access token configured")
            return False
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'

        }
        template_url = f"{base_url}/messages"

        template_payload = {

            'messaging_product': "whatsapp",
            'to': phone_number,
            "type": "text",
            "text": {
                'body': message,
            }

        }
        try:
            response = requests.post(template_url, headers=headers, json=template_payload)
            response.raise_for_status()  # Raise an exception for HTTP errors

            self.service_request_id.message_post(body=_(
                "WhatsApp Job card %s Cancelled Insp.Charges by CST message sent successfully to the customer") % self.name)
            return True

        except requests.exceptions.RequestException as e:
            _logger.error("❌ WhatsApp message failed: %s", str(e))
            # Optionally, notify the user or log the error in the chatter
            self.service_request_id.message_post(
                body=_("WhatsApp Failed message sent successfully to %s") % self.partner_id.name,
                message_type='notification',

            )
            return False

    # this is currently working commented by vijaya bhaskar or july 09-2025
    # @api.onchange('job_state')
    # def _onchange_job_card_state_status(self):
    #     for rec in self:
    #
    #         rec.job_card_state = rec.job_state.name
    #         rec.job_card_state_code = rec.job_state.code
    #         rec.service_request_id.service_request_state = rec.job_state.name
    #         rec.service_request_id.service_request_state_code = rec.job_state.code
    #         rec.service_request_id.state  = rec.job_state
    #
    #         if rec._origin and rec.job_state != rec._origin.job_state:
    #
    #             ''' Technician Accepted state'''
    #             if rec.job_card_state_code =='103':
    #                 rec.technician_accepted_date = fields.Datetime.now()
    #                 if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                     state_lst = []
    #                     job_state_update = self.env['project.task.type'].search([('code', 'in', ['103', '104', '105', '106', '107'])])
    #                     for job_state in job_state_update:
    #                         state_lst.append(job_state.id)
    #                         rec.job_state = self.job_state
    #                         print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                     if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                         if state_lst:
    #                             rec.available_state_ids = [(6, 0, state_lst)]
    #                         else:
    #                             rec.available_state_ids = [(5,)]
    #                     else:
    #                         rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #                 if self.env.user.has_group('machine_repair_management.group_job_card_back_office_user'):
    #                     state_lst = []
    #                     job_state_back_office = self.env['project.task.type'].search([('code','in',('105','106','107'))])
    #                     for job in job_state_back_office:
    #                         state_lst.append(job.id)
    #                         rec.job_state = self.job_state
    #                     if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                         if state_lst:
    #                             rec.available_state_ids =[(6,0,state_lst)]
    #                         else:
    #                             rec.available_state_ids = [(5,)]
    #
    #                     else:
    #                         rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #             ''' Technician Rejected state'''
    #             if rec.job_card_state_code == '104':
    #                 rec.technician_rejected_date = fields.Datetime.now()
    #                 if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                     state_lst = []
    #                     job_state_update = self.env['project.task.type'].search([('code', 'in', ['104', '107'])])
    #                     for job_state in job_state_update:
    #                         state_lst.append(job_state.id)
    #                         rec.job_state = self.job_state
    #                         print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                     if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                         if state_lst:
    #                             rec.available_state_ids = [(6, 0, state_lst)]
    #                         else:
    #                             rec.available_state_ids = [(5,)]
    #                     else:
    #                         rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #
    #                 if self.env.user.has_group('machine_repair_management.group_job_card_back_office_user'):
    #                     state_lst = []
    #                     job_state_back_office = self.env['project.task.type'].search([('code','in',('107'))])
    #                     for job in job_state_back_office:
    #                         state_lst.append(job.id)
    #                         rec.job_state = self.job_state
    #                     if hasattr(eec, available_state_ids) and rec.available_state_ids:
    #                         if state_lst:
    #                             rec.available_state_ids =[(6,0,state_lst)]
    #                         else:
    #                             rec.available_state_ids = [(5,)]
    #
    #                     else:
    #                         rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #             ''' Failed to attend call (Customer not answered) '''
    #             if rec.job_card_state_code == '105':
    #                 if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                     state_lst = []
    #                     job_state_update = self.env['project.task.type'].search([('code', 'in', ['105', '107'])])
    #                     for job_state in job_state_update:
    #                         state_lst.append(job_state.id)
    #                         rec.job_state = self.job_state
    #                         print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                     if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                         if state_lst:
    #                             rec.available_state_ids = [(6, 0, state_lst)]
    #                         else:
    #                             rec.available_state_ids = [(5,)]
    #                     else:
    #                         rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #
    #                 if self.env.user.has_group('machine_repair_management.group_job_card_back_office_user'):
    #                     state_lst = []
    #                     job_state_update = self.env['project.task.type'].search([('code', 'in', ['107'])])
    #                     for job_state in job_state_update:
    #                         state_lst.append(job_state.id)
    #                         rec.job_state = self.job_state
    #                         print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                     if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                         if state_lst:
    #                             rec.available_state_ids = [(6, 0, state_lst)]
    #                         else:
    #                             rec.available_state_ids = [(5,)]
    #                     else:
    #                         rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #             ''' Out of City '''
    #             if rec.job_card_state_code == '106':
    #                 if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                     state_lst = []
    #                     job_state_update = self.env['project.task.type'].search([('code', 'in', ['106', '107'])])
    #                     for job_state in job_state_update:
    #                         state_lst.append(job_state.id)
    #                         rec.job_state = self.job_state
    #                         print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                     if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                         if state_lst:
    #                             rec.available_state_ids = [(6, 0, state_lst)]
    #                         else:
    #                             rec.available_state_ids = [(5,)]
    #                     else:
    #                         rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #                 if self.env.user.has_group('machine_repair_management.group_job_card_back_office_user'):
    #                     state_lst = []
    #                     job_state_update = self.env['project.task.type'].search([('code', 'in', ['107'])])
    #                     for job_state in job_state_update:
    #                         state_lst.append(job_state.id)
    #                         rec.job_state = self.job_state
    #                         print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                     if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                         if state_lst:
    #                             rec.available_state_ids = [(6, 0, state_lst)]
    #                         else:
    #                             rec.available_state_ids = [(5,)]
    #                     else:
    #                         rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #
    #             ''' Rescheduled State '''
    #             if rec.job_card_state_code == '107':
    #                 if self.env.user.has_group('machine_repair_management.group_job_card_back_office_user'):
    #                     state_lst = []
    #                     job_state_update = self.env['project.task.type'].search([('code', 'in', ['103', '104'])])
    #                     for job_state in job_state_update:
    #                         state_lst.append(job_state.id)
    #                         rec.job_state = self.job_state
    #                         print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                     if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                         if state_lst:
    #                             rec.available_state_ids = [(6, 0, state_lst)]
    #                         else:
    #                             rec.available_state_ids = [(5,)]
    #                     else:
    #                         rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #
    #
    #
    #             ''' Customer Accepted State '''
    #             if rec.job_card_state_code == '108':
    #                 if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                     state_lst = []
    #                     job_state_update = self.env['project.task.type'].search([('code', 'in', ['103', '107', '108'])])
    #                     for job_state in job_state_update:
    #                         state_lst.append(job_state.id)
    #                         rec.job_state = self.job_state
    #                         print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                     if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                         if state_lst:
    #                             rec.available_state_ids = [(6, 0, state_lst)]
    #                         else:
    #                             rec.available_state_ids = [(5,)]
    #                     else:
    #                         rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #             ''' Technician Started State '''
    #             if rec.job_card_state_code =='109':
    #                 rec.technician_started_date = fields.Datetime.now()
    #                 if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                     state_lst = []
    #                     job_state_update = self.env['project.task.type'].search([('code', 'in', ['107', '109', '110'])])
    #                     for job_state in job_state_update:
    #                         state_lst.append(job_state.id)
    #                         rec.job_state = self.job_state
    #                         print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                     if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                         if state_lst:
    #                             rec.available_state_ids = [(6, 0, state_lst)]
    #                         else:
    #                             rec.available_state_ids = [(5,)]
    #                     else:
    #                         rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #             ''' Technician Reached State '''
    #             if rec.job_card_state_code == '110':
    #                 rec.technician_reached_date = fields.Datetime.now()
    #                 if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                     state_lst = []
    #                     job_state_update = self.env['project.task.type'].search([('code', 'in', ['110', '111'])])
    #                     for job_state in job_state_update:
    #                         state_lst.append(job_state.id)
    #                         rec.job_state = self.job_state
    #                         print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                     if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                         if state_lst:
    #                             rec.available_state_ids = [(6, 0, state_lst)]
    #                         else:
    #                             rec.available_state_ids = [(5,)]
    #                     else:
    #                         rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #             ''' Warranty Verification State '''
    #             if rec.job_card_state_code == '111':
    #                 if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                     state_lst = []
    #                     job_state_update = self.env['project.task.type'].search([('code', 'in', ['111', '112', '113', '114'])])
    #                     for job_state in job_state_update:
    #                         state_lst.append(job_state.id)
    #                         rec.job_state = self.job_state
    #                         print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                     if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                         if state_lst:
    #                             rec.available_state_ids = [(6, 0, state_lst)]
    #                         else:
    #                             rec.available_state_ids = [(5,)]
    #                     else:
    #                         rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #             ''' Inspection Started State '''
    #             if rec.job_card_state_code == '113':
    #                 if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                     state_lst = []
    #                     job_state_update = self.env['project.task.type'].search([('code', 'in', ['113', '114', '125'])])
    #                     for job_state in job_state_update:
    #                         state_lst.append(job_state.id)
    #                         rec.job_state = self.job_state
    #                         print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                     if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                         if state_lst:
    #                             rec.available_state_ids = [(6, 0, state_lst)]
    #                         else:
    #                             rec.available_state_ids = [(5,)]
    #                     else:
    #                         rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #             ''' Quotation provided. Waiting customer approval State '''
    #             if rec.job_card_state_code == '114':
    #                 if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                     state_lst = []
    #                     job_state_update = self.env['project.task.type'].search([('code', 'in', ['114', '115', '116'])])
    #                     for job_state in job_state_update:
    #                         state_lst.append(job_state.id)
    #                         rec.job_state = self.job_state
    #                         print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                     if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                         if state_lst:
    #                             rec.available_state_ids = [(6, 0, state_lst)]
    #                         else:
    #                             rec.available_state_ids = [(5,)]
    #                     else:
    #                         rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #
    #
    #                 if self.env.user.has_group('machine_repair_management.group_job_card_back_office_user'):
    #                     state_lst = []
    #                     job_state_update = self.env['project.task.type'].search([('code', 'in', ['124'])])
    #                     for job_state in job_state_update:
    #                         state_lst.append(job_state.id)
    #                         rec.job_state = self.job_state
    #                         print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                     if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                         if state_lst:
    #                             rec.available_state_ids = [(6, 0, state_lst)]
    #                         else:
    #                             rec.available_state_ids = [(5,)]
    #                     else:
    #                         rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #             ''' Job Started (In-progress) State '''
    #             if rec.job_card_state_code == '115':
    #                 rec.job_started_date = fields.Datetime.now()
    #                 if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                     state_lst = []
    #                     job_state_update = self.env['project.task.type'].search([('code', 'in', ['115', '117', '121'])])
    #                     for job_state in job_state_update:
    #                         state_lst.append(job_state.id)
    #                         rec.job_state = self.job_state
    #                         print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                     if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                         if state_lst:
    #                             rec.available_state_ids = [(6, 0, state_lst)]
    #                         else:
    #                             rec.available_state_ids = [(5,)]
    #                     else:
    #                         rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #
    #             ''' Payment Refused State '''
    #             if rec.job_card_state_code =='116':
    #                 # rec.job_started_date = fields.Datetime.now()
    #                 if self.env.user.has_group('machine_repair_management.group_job_card_back_office_user'):
    #                     state_lst = []
    #                     job_state_update = self.env['project.task.type'].search([('code', 'in', ['107', '124'])])
    #                     for job_state in job_state_update:
    #                         state_lst.append(job_state.id)
    #                         rec.job_state = self.job_state
    #                         print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                     if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                         if state_lst:
    #                             rec.available_state_ids = [(6, 0, state_lst)]
    #                         else:
    #                             rec.available_state_ids = [(5,)]
    #                     else:
    #                         rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #
    #             ''' Unit Pull Out State '''
    #             if rec.job_card_state_code == '117':
    #                 if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                     state_lst = []
    #                     job_state_update = self.env['project.task.type'].search([('code', 'in', ['117', '121'])])
    #                     for job_state in job_state_update:
    #                         state_lst.append(job_state.id)
    #                         rec.job_state = self.job_state
    #                         print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                     if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                         if state_lst:
    #                             rec.available_state_ids = [(6, 0, state_lst)]
    #                         else:
    #                             rec.available_state_ids = [(5,)]
    #                     else:
    #                         rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #                 if self.env.user.has_group('machine_repair_management.group_job_card_back_office_user'):
    #                     state_lst = []
    #                     job_state_update = self.env['project.task.type'].search([('code', 'in', ['123', '124','107'])])
    #                     for job_state in job_state_update:
    #                         state_lst.append(job_state.id)
    #                         rec.job_state = self.job_state
    #                         print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                     if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                         if state_lst:
    #                             rec.available_state_ids = [(6, 0, state_lst)]
    #                         else:
    #                             rec.available_state_ids = [(5,)]
    #                     else:
    #                         rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #             ''' On Hold - Spare Parts Required State '''
    #             if rec.job_card_state_code == '121':
    #                 rec.job_hold_date = fields.Datetime.now()
    #                 if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                     state_lst = []
    #                     job_state_update = self.env['project.task.type'].search([('code', 'in', ['121', '123', '124'])])
    #                     for job_state in job_state_update:
    #                         state_lst.append(job_state.id)
    #                         rec.job_state = self.job_state
    #                         print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                     if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                         if state_lst:
    #                             rec.available_state_ids = [(6, 0, state_lst)]
    #                         else:
    #                             rec.available_state_ids = [(5,)]
    #                     else:
    #                         rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #
    #                 if self.env.user.has_group('machine_repair_management.group_job_card_back_office_user'):
    #                     state_lst = []
    #                     job_state_update = self.env['project.task.type'].search([('code', 'in', ['123', '107'])])
    #                     for job_state in job_state_update:
    #                         state_lst.append(job_state.id)
    #                         rec.job_state = self.job_state
    #                         print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                     if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                         if state_lst:
    #                             rec.available_state_ids = [(6, 0, state_lst)]
    #                         else:
    #                             rec.available_state_ids = [(5,)]
    #                     else:
    #                         rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #             ''' Parts Ready State '''
    #             if rec.job_card_state_code in ('122','123'):
    #                 rec.job_resume_date = fields.Datetime.now()
    #
    #                 if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                     state_lst = []
    #                     job_state_update = self.env['project.task.type'].search([('code', 'in', ['122', '123', '107'])])
    #                     for job_state in job_state_update:
    #                         state_lst.append(job_state.id)
    #                         rec.job_state = self.job_state
    #                         print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                     if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                         if state_lst:
    #                             rec.available_state_ids = [(6, 0, state_lst)]
    #                         else:
    #                             rec.available_state_ids = [(5,)]
    #                     else:
    #                         rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #             ''' Ready to Invoice (Complete) State '''
    #             if rec.job_card_state_code == '125':
    #                 rec.closed_datetime = fields.Datetime.now()
    #                 if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                     state_lst = []
    #                     job_state_update = self.env['project.task.type'].search([('code', 'in', ['125', '126'])])
    #                     for job_state in job_state_update:
    #                         state_lst.append(job_state.id)
    #                         rec.job_state = self.job_state
    #                         print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                     if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                         if state_lst:
    #                             rec.available_state_ids = [(6, 0, state_lst)]
    #                         else:
    #                             rec.available_state_ids = [(5,)]
    #                     else:
    #                         rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #                 if self.env.user.has_group('machine_repair_management.group_job_card_back_office_user'):
    #                     state_lst = []
    #                     job_state_update = self.env['project.task.type'].search([('code', 'in', ['126'])])
    #                     for job_state in job_state_update:
    #                         state_lst.append(job_state.id)
    #                         rec.job_state = self.job_state
    #                         print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                     if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                         if state_lst:
    #                             rec.available_state_ids = [(6, 0, state_lst)]
    #                         else:
    #                             rec.available_state_ids = [(5,)]
    #                     else:
    #                         rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #             '''If the User selected the Parts Ready Job state then check all the parts should be ticked in the product consume parts/service by Vijaya Bhaskar on June-30-2025'''
    #             if rec.job_card_state_code in ('122','126'):
    #                 if rec.product_line_ids:
    #                     for line in rec.product_line_ids:
    #                         if line.product_id:
    #                             if not line.parts_reserved_bool:
    #                                 raise ValidationError("Please check all the Products should be Reserved.This Product %s is not reserved" % line.product_id.display_name)
    #
    #                         if line.on_hand_qty == 0.0:
    #                             raise ValidationError("Please Stock is not available.Please Contact Administrator")
    #
    #
    #
    #
    #                 if not rec.product_line_ids:
    #                     raise ValidationError("Please give any one of the Product in the product consume Part/services")
    #
    #
    #             ''' Scheduled (Technician Assigned) State '''
    #             if rec.job_card_state_code == '102':
    #                 if not rec.team_id :
    #                     raise ValidationError("Please enter Team Leader name in the Job card")
    #
    #                 if not rec.technician_id:
    #
    #                     raise ValidationError("Please Enter Technician Name ")
    #
    #                 if self.env.user.has_group('machine_repair_management.group_job_card_back_office_user'):
    #                     state_lst = []
    #                     job_state_back_office = self.env['project.task.type'].search([('code','in',('102','103','104'))])
    #                     for job in job_state_back_office:
    #                         state_lst.append(job.id)
    #                         rec.job_state = self.job_state
    #                     if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                         if state_lst:
    #                             rec.available_state_ids =[(6,0,state_lst)]
    #                         else:
    #                             rec.available_state_ids = [(5,)]
    #
    #                     else:
    #                         rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #
    #
    #
    #
    #             elif rec.job_card_state  not in  ('101','102'):
    #
    #                 if not rec.team_id :
    #
    #                     raise ValidationError("Please give the Team Leader Name")
    #
    #                 if not rec.technician_id:
    #
    #                     raise ValidationError("Please Enter Technician Name ")
    #
    #                 # if not rec.service_requested_datetime:
    #                 #
    #                 #     raise ValidationError("Please Enter  Requested Date & Time")
    #
    #                 '''Commented on Jun - 7 -2025 for replace appointment datetime with planned_date_begin for scheduling'''
    #                 # if not rec.appointment_datetime:
    #                 #     raise ValidationError("Please Enter Appointment Date & Time")
    #                 if not rec.planned_date_begin:
    #
    #                     raise ValidationError("Please Enter Appt Start Date & Time")
    #
    #                 if rec.job_card_state_code == '126':
    #                     if not rec.closed_datetime:
    #                         raise ValidationError("Please Enter Closed Date time.")
    #                     if rec.quotation_count == 0:
    #                     # if not rec.sale_id:
    #                         if rec.product_line_ids:
    #                             for line in rec.product_line_ids:
    #                                 if not line.under_warranty_bool:
    #                                     raise ValidationError("Complete your quotation first, then close the job card")
    #
    #
    #                     # if self.env.user.has_group('machine_repair_management.group_technical_allocation_user'):
    #                     #     if not rec.supervisor_comments:
    #                     #         raise ValidationError("Please give the supervisor comments for this Job card")
    #

    ''' it will be faster loaded job card in'''
    # def _get_task_type_by_code(self):
    #     global _task_type_cache
    #     # Initialize cache if not already set
    #     if _task_type_cache is None:
    #         task_types = self.env['project.task.type'].search([
    #             ('code', '!=', False)
    #         ])
    #         _task_type_cache = {t.code: t.id for t in task_types}
    #     return _task_type_cache
    #
    # def _clear_task_type_cache(self):
    #     """Clear the module-level cache when needed (e.g., after modifying project.task.type)."""
    #     global _task_type_cache
    #     _task_type_cache = None

    # def read(self, fields=None, load='_classic_read'):
    #     res = super(ProjectTask, self).read(fields, load)
    #     # Only compute if available_state_ids is requested in fields
    #     if not fields or 'available_state_ids' in fields:
    #         self._compute_available_state_ids()
    #     # if not fields or 'address' in fields:
    #     #     self._compute_address()
    #     return res

    current_user_id = fields.Many2one('res.users', compute='_compute_current_user', store=False)

    parts_user_bool = fields.Boolean(string="Parts User", default=False, compute="_compute_current_user")

    def _compute_current_user(self):
        for rec in self:
            rec.current_user_id = self.env.user.id
            '''code is added on Oct 22-2025 for parts user should not see the Create Quotation,Create work order copy,send proforma invoice'''
            rec.parts_user_bool = False
            if rec.current_user_id.has_group('machine_repair_management.group_parts_user'):
                rec.parts_user_bool = True

    create_quotation_show_bool = fields.Boolean(string="Show Quotation Button", default=False)

    '''this code is correctly work but they want dynamic work added on the project task stages commented on Oct 28 2025'''

    # @api.depends('job_card_state_code', 'current_user_id')
    # def _compute_available_state_ids(self):
    #     # Define state transitions based on job_card_state_code and user groups
    #     state_transitions = {
    #
    #         # for parallel run  only it will be commented on sep 26 2025
    #         # '101': {
    #         #     'machine_repair_management.group_job_card_back_office_user': ['101', '102','124'],
    #         #
    #         # },
    #
    #          '101': {
    #             # 'machine_repair_management.group_job_card_back_office_user': ['101', '102','124'],
    #             'machine_repair_management.group_job_card_back_office_user':['101','102','129', '131','114','127', '128',  '121','122', '107', '125','124','112','126'],
    #
    #
    #             # 'machine_repair_management.group_technical_allocation_user':['101','102','121','124','125','126'],
    #             'machine_repair_management.group_technical_allocation_user':['101','102','129', '131','114','127', '128',  '121','122', '107', '125','124','112','126'],
    #
    #             # 'machine_repair_management.group_technical_allocation_user':['121','124','125','126'],
    #
    #
    #         },
    #         '102': {
    #              'machine_repair_management.group_job_card_mobile_user': ['102', '103', '104'],
    #             # 'machine_repair_management.group_job_card_back_office_user': ['102', '103', '104','124'],
    #             # 'machine_repair_management.group_technical_allocation_user':['102','121','124','125','126'],
    #              'machine_repair_management.group_job_card_back_office_user':['101','102','129', '131','114','127', '128',  '121','122', '107', '125','124','112','126'],
    #
    #
    #         },
    #         ## After technician Accepted client asked to change the status as follows on Sep 23-2025
    #             # • Once the technician accepts a job card, the following status options should become available:
    #             # ◦ Customer Accepted
    #             # ◦ Reschedule
    #             # ◦ Cancellation
    #         '103': {
    #
    #             'machine_repair_management.group_job_card_mobile_user': ['103', '108', '107','124'],
    #             # 'machine_repair_management.group_job_card_mobile_user': ['103', '104', '105', '106', '107', '108','124'],
    #             'machine_repair_management.group_job_card_back_office_user': ['105', '106', '107', '108'],
    #
    #         },
    #         '104': {
    #             'machine_repair_management.group_job_card_mobile_user': ['104'],
    #             # 'machine_repair_management.group_job_card_mobile_user': ['104', '107'],
    #
    #             'machine_repair_management.group_job_card_back_office_user': ['104','107'],
    #         },
    #         '105': {
    #             'machine_repair_management.group_job_card_mobile_user': ['105', '107'],
    #             'machine_repair_management.group_job_card_back_office_user': ['107'],
    #         },
    #         '106': {
    #             'machine_repair_management.group_job_card_mobile_user': ['106', '107'],
    #             'machine_repair_management.group_job_card_back_office_user': ['107'],
    #         },
    #         '107': {
    #
    #             'machine_repair_management.group_job_card_mobile_user': ['107'],
    #              'machine_repair_management.group_job_card_back_office_user':['101','102','129', '131','114','127', '128',  '121','122', '107', '125','124','112','126'],
    #
    #             # 'machine_repair_management.group_job_card_back_office_user': ['103', '104'],
    #         },
    #         '108': {
    #             'machine_repair_management.group_job_card_mobile_user': ['108', '109'],
    #         },
    #         '109': {
    #             'machine_repair_management.group_job_card_mobile_user': ['109', '110'],
    #         },
    #         '110': {
    #             'machine_repair_management.group_job_card_mobile_user': ['110', '111'],
    #
    #
    #         },
    #      # Dated on Sep 23-2025
    #         #      After the technician completes Warranty Verification, update the job card status to one of the following:
    #         # ◦ Inspection Started
    #         # ◦ Cancelled – Inspection Charge Rejected by Customer
    #         # '111': {
    #         #     'machine_repair_management.group_job_card_mobile_user': ['111', '112', '113', '114', '115'],
    #         # },
    #         '111': {
    #             'machine_repair_management.group_job_card_mobile_user': ['111', '113', '112'],
    #         },
    #         '112': {
    #             'machine_repair_management.group_job_card_mobile_user': ['112', '124'],
    #             # 'machine_repair_management.group_job_card_back_office_user': ['112', '124', '126'],
    #             'machine_repair_management.group_job_card_back_office_user':['101','102','129', '131','114','127', '128',  '121','122', '107', '125','124','112','126'],
    #
    #         },
    #
    #         # Dated on Sep 23-2025
    #         # When the technician clicks “Inspection Started”, the system should display a list of status options:
    #         # ◦ Ready to Invoice
    #         # ◦ On Hold – Sp Req
    #         # ◦ Customer Need Quote
    #         # ◦ Unit Pull Out
    #         # ◦ Customer Reject Service
    #         # ◦ Payment Refused
    #         # ◦ Request – Revisit
    #         # • Make Symptoms , Defects, Service tabs are mandatory.
    #         # '113': {
    #         #     'machine_repair_management.group_job_card_mobile_user': ['113', '121', '114', '125'],
    #         # },
    #         '113': {
    #             'machine_repair_management.group_job_card_mobile_user': ['113', '125','121', '129','117','130','116','107'],
    #         },
    #
    #         '114': {
    #             'machine_repair_management.group_job_card_mobile_user': ['114', '127', '128'],
    #             # 'machine_repair_management.group_job_card_back_office_user': ['114', '115', '116', '124', '127', '128'],
    #             'machine_repair_management.group_job_card_back_office_user':['101','102','129', '131','114','127', '128',  '121','122', '107', '125','124','112','126'],
    #
    #         },
    #         '115': {
    #             'machine_repair_management.group_job_card_mobile_user': ['115', '117', '121', '125'],
    #             'machine_repair_management.group_job_card_back_office_user': ['115', '117', '121', '125'],
    #
    #         },
    #         '116': {
    #             'machine_repair_management.group_job_card_mobile_user': ['116'],
    #             # 'machine_repair_management.group_job_card_mobile_user': ['116', '107'],
    #
    #             'machine_repair_management.group_job_card_back_office_user': ['116', '107', '124'],
    #
    #         },
    #         '117': {
    #             'machine_repair_management.group_job_card_mobile_user': ['117'],
    #             # 'machine_repair_management.group_job_card_mobile_user': ['117', '121'],
    #
    #             'machine_repair_management.group_job_card_back_office_user': ['117','107', '123', '124'],
    #         },
    #         '121': {
    #             'machine_repair_management.group_job_card_mobile_user': ['121'],
    #             # 'machine_repair_management.group_job_card_mobile_user': ['121', '123', '124'],
    #              'machine_repair_management.group_job_card_back_office_user':['101','102','129', '131','114','127', '128',  '121','122', '107', '125','124','112','126'],
    #
    #             # 'machine_repair_management.group_job_card_back_office_user': ['121', '122', '107', '123', '125'],
    #             'machine_repair_management.group_parts_user':['121', '122'],
    #             # 'machine_repair_management.group_technical_allocation_user':['121','124','125','126'],
    #
    #         },
    #         '122': {
    #             'machine_repair_management.group_job_card_mobile_user': ['122', '123', '107'],
    #             # 'machine_repair_management.group_technical_allocation_user': ['122', '124', '125','126'],
    #              'machine_repair_management.group_job_card_back_office_user':['101','102','129', '131','114','127', '128',  '121','122', '107', '125','124','112','126'],
    #
    #
    #         },
    #         '123': {
    #             'machine_repair_management.group_job_card_mobile_user': ['123', '125', '107'],
    #             'machine_repair_management.group_job_card_back_office_user': ['123', '125'],
    #
    #         },
    #          '124': {
    #             'machine_repair_management.group_job_card_back_office_user':['101','102','129', '131','114','127', '128',  '121','122', '107', '125','124','112','126'],
    #
    #         },
    #
    #
    #         '125': {
    #             'machine_repair_management.group_job_card_mobile_user': ['125','126','124'],
    #             'machine_repair_management.group_job_card_back_office_user':['101','102','129', '131','114','127', '128',  '121','122', '107', '125','124','112','126'],
    #
    #             # 'machine_repair_management.group_job_card_back_office_user': ['125', '124', '126'],
    #         },
    #          '126': {
    #             'machine_repair_management.group_job_card_back_office_user':['101','102','129', '131','114','127', '128',  '121','122', '107', '125','124','112','126'],
    #
    #             # 'machine_repair_management.group_job_card_back_office_user': ['125', '124', '126'],
    #         },
    #
    #          '127': {
    #             'machine_repair_management.group_job_card_mobile_user': ['127'],
    #             # 'machine_repair_management.group_job_card_mobile_user': ['127', '115', '116','121'],
    #
    #             # 'machine_repair_management.group_job_card_back_office_user': ['127', '115', '116'],
    #             'machine_repair_management.group_job_card_back_office_user':['101','102','129', '131','114','127', '128',  '121','122', '107', '125','124','112','126'],
    #
    #         },
    #           '128': {
    #             'machine_repair_management.group_job_card_mobile_user': ['128', '124', '107'],
    #             # 'machine_repair_management.group_job_card_back_office_user': ['128', '124', '107'],
    #             'machine_repair_management.group_job_card_back_office_user':['101','102','129', '131','114','127', '128',  '121','122', '107', '125','124','112','126'],
    #
    #         },
    #           '129':{
    #
    #               'machine_repair_management.group_parts_user':['129', '131'],
    #               'machine_repair_management.group_job_card_mobile_user': ['129'],
    #              'machine_repair_management.group_job_card_back_office_user':['101','102','129', '131','114','127', '128',  '121','122', '107', '125','124','112','126'],
    #
    #               },
    #
    #           '130':{
    #
    #               'machine_repair_management.group_job_card_mobile_user': ['130'],
    #
    #               },
    #
    #           '131':{
    #                 # 'machine_repair_management.group_technical_allocation_user':['131','107','124'],
    #                 'machine_repair_management.group_job_card_back_office_user':['101','102','129', '131','114','127', '128',  '121','122', '107', '125','124','112','126'],
    #
    #               }
    #
    #     }
    #
    #     # Fetch all project.task.type records at once
    #     # type_by_code = self._get_task_type_by_code()
    #     ''' this is currently worked correctly
    #     task_types = self.env['project.task.type'].search([('code', 'in', list(set(code for transitions in state_transitions.values() for codes in transitions.values() for code in codes)))])
    #     type_by_code = {t.code: t.id for t in task_types}
    #     '''
    #
    #     all_codes = list(set(code for transitions in state_transitions.values() for codes in transitions.values() for code in codes))
    #     task_types = self.env['project.task.type'].search([('code', 'in', all_codes)])
    #     type_by_code = {t.code: t.id for t in task_types}
    #
    #
    #     # Check user groups once
    #     is_back_office = self.env.user.has_group('machine_repair_management.group_job_card_back_office_user')
    #     is_mobile = self.env.user.has_group('machine_repair_management.group_job_card_mobile_user')
    #     is_parts_officer = self.env.user.has_group('machine_repair_management.group_parts_user')
    #     is_technical_officer = self.env.user.has_group('machine_repair_management.group_technical_allocation_user')
    #
    #     # Process each record
    #     for rec in self:
    #         rec.available_state_ids = [(5,)]  # Clear existing states
    #         if not rec.job_card_state_code or rec.job_card_state_code not in state_transitions:
    #             continue
    #         # if not rec.job_card_state_code:
    #         #     continue
    #
    #         # Get allowed state codes based on user groups
    #         if rec.job_card_state_code == '110' and rec.second_visit_technician_bool:
    #             current_transitions = copy.deepcopy(state_transitions)
    #             current_transitions['110']['machine_repair_management.group_job_card_mobile_user'] = ['110','125']
    #         else:
    #             current_transitions = state_transitions
    #
    #         allowed_codes = []
    #
    #         # Find allowed states based on user group
    #         if rec.job_card_state_code in current_transitions:
    #             if (
    #                 is_parts_officer
    #                 and rec.current_user_id.has_group('machine_repair_management.group_parts_user')
    #                 and 'machine_repair_management.group_parts_user' in current_transitions[rec.job_card_state_code]
    #             ):
    #                 allowed_codes.extend(current_transitions[rec.job_card_state_code]['machine_repair_management.group_parts_user'])
    #
    #             elif (
    #                 is_technical_officer
    #                 and rec.current_user_id.has_group('machine_repair_management.group_technical_allocation_user')
    #                 and 'machine_repair_management.group_technical_allocation_user' in current_transitions[rec.job_card_state_code]
    #             ):
    #                 allowed_codes.extend(current_transitions[rec.job_card_state_code]['machine_repair_management.group_technical_allocation_user'])
    #
    #             else:
    #                 if (
    #                     is_back_office
    #                     and rec.current_user_id.has_group('machine_repair_management.group_job_card_back_office_user')
    #                     and 'machine_repair_management.group_job_card_back_office_user' in current_transitions[rec.job_card_state_code]
    #                 ):
    #                     allowed_codes.extend(current_transitions[rec.job_card_state_code]['machine_repair_management.group_job_card_back_office_user'])
    #
    #                 elif (
    #                     is_mobile
    #                     and rec.current_user_id.has_group('machine_repair_management.group_job_card_mobile_user')
    #                     and 'machine_repair_management.group_job_card_mobile_user' in current_transitions[rec.job_card_state_code]
    #                 ):
    #                     allowed_codes.extend(current_transitions[rec.job_card_state_code]['machine_repair_management.group_job_card_mobile_user'])
    #
    #         ##### working code commented on Oct 24 due to second time visit they don't ask again warranty verification
    #         # allowed_codes = []
    #         # if rec.job_card_state_code in state_transitions:
    #         #
    #         #     if is_parts_officer and rec.current_user_id.has_group('machine_repair_management.group_parts_user') and 'machine_repair_management.group_parts_user' in state_transitions[rec.job_card_state_code]:
    #         #         # If the user is a parts user, only use parts user states
    #         #         allowed_codes.extend(state_transitions[rec.job_card_state_code]['machine_repair_management.group_parts_user'])
    #         #     elif is_technical_officer and rec.current_user_id.has_group('machine_repair_management.group_technical_allocation_user') and 'machine_repair_management.group_technical_allocation_user' in state_transitions[rec.job_card_state_code]:
    #         #         allowed_codes.extend(state_transitions[rec.job_card_state_code]['machine_repair_management.group_technical_allocation_user'])
    #         #     else:
    #         #         #     # Normal transition logic for other cases
    #         #         if is_back_office and rec.current_user_id.has_group('machine_repair_management.group_job_card_back_office_user') and 'machine_repair_management.group_job_card_back_office_user' in state_transitions[rec.job_card_state_code]:
    #         #             allowed_codes.extend(state_transitions[rec.job_card_state_code]['machine_repair_management.group_job_card_back_office_user'])
    #         #         elif is_mobile and rec.current_user_id.has_group('machine_repair_management.group_job_card_mobile_user') and 'machine_repair_management.group_job_card_mobile_user' in state_transitions[rec.job_card_state_code]:
    #         #             allowed_codes.extend(state_transitions[rec.job_card_state_code]['machine_repair_management.group_job_card_mobile_user'])
    #         #
    #
    #         # Map state codes to IDs
    #         ''' currently working commented on Oct-08-2025 for ordered sequence
    #         state_ids = [type_by_code[code] for code in set(allowed_codes) if code in type_by_code]
    #         if state_ids:
    #             rec.available_state_ids = [(6, 0, state_ids)]
    #
    #         '''
    #         state_ids = []
    #
    #         for code in allowed_codes:
    #             if code in type_by_code:
    #                 state_ids.append(type_by_code[code])
    #         if state_ids:
    #             ordered_records = self.env['project.task.type'].browse(state_ids)
    #
    #             ordered_records = ordered_records.sorted(
    #                 key=lambda r: allowed_codes.index(r.code) if r.code in allowed_codes else 999
    #             )
    #
    #             for idx, rec_type in enumerate(ordered_records):
    #                 rec_type.sequence = idx  # Forces the display order in the statusbar
    #
    #             rec.available_state_ids = [(6, 0, ordered_records.ids)]

    @api.depends('job_card_state_code', 'current_user_id')
    def _compute_available_state_ids(self):
        """
        Dynamically computes available state transitions per record
        based on project.task.type fields:
          - back_office_user_code
          - mobile_user_code
          - parts_user_code
        """

        task_types = self.env['project.task.type'].search([])
        type_by_code = {t.code: t for t in task_types}

        state_transitions = {}
        for task_type in task_types:
            domain_backoffice = (
                task_type.back_office_user_code.split(",") if task_type.back_office_user_code else []
            )
            domain_mobile = (
                task_type.mobile_user_code.split(",") if task_type.mobile_user_code else []
            )
            domain_parts = (
                task_type.parts_user_code.split(",") if task_type.parts_user_code else []
            )

            # Construct per-code dynamic transitions
            state_transitions[task_type.code] = {
                'machine_repair_management.group_job_card_back_office_user': domain_backoffice,
                'machine_repair_management.group_technical_allocation_user': domain_backoffice,
                'machine_repair_management.group_job_card_mobile_user': domain_mobile,
                'machine_repair_management.group_parts_user': domain_parts,
            }

        # Pre-check group membership to avoid multiple SQL hits
        user = self.env.user
        group_backoffice = 'machine_repair_management.group_job_card_back_office_user'
        group_mobile = 'machine_repair_management.group_job_card_mobile_user'
        group_parts = 'machine_repair_management.group_parts_user'
        group_technical = 'machine_repair_management.group_technical_allocation_user'

        is_backoffice = user.has_group(group_backoffice)
        is_mobile = user.has_group(group_mobile)
        is_parts = user.has_group(group_parts)
        is_technical = user.has_group(group_technical)

        # Loop through each record to assign allowed states
        for rec in self:
            rec.available_state_ids = [(5, 0, 0)]  # clear

            if not rec.job_card_state_code:
                continue

            current_transitions = copy.deepcopy(state_transitions)

            if rec.job_card_state_code == '110':
                if rec.second_visit_technician_bool:
                    current_transitions['110']['machine_repair_management.group_job_card_mobile_user'] = ['110', '125']

            # Fetch transitions for this record
            transitions = current_transitions.get(rec.job_card_state_code)

            # transitions = state_transitions.get(rec.job_card_state_code)
            if not transitions:
                continue

            allowed_codes = []

            # Match group-based allowed transitions

            if is_parts and group_parts in transitions:
                allowed_codes += transitions[group_parts]
            elif is_technical and group_technical in transitions:
                allowed_codes += transitions[group_technical]

            elif is_backoffice and group_backoffice in transitions:
                allowed_codes += transitions[group_backoffice]
            elif is_mobile and group_mobile in transitions:
                allowed_codes += transitions[group_mobile]

            # Remove duplicates while keeping order
            seen = set()
            allowed_codes = [x for x in allowed_codes if not (x in seen or seen.add(x))]

            # Map codes to records
            if allowed_codes:
                allowed_states = self.env['project.task.type'].search([('code', 'in', allowed_codes)])
                ordered_states = allowed_states.sorted(
                    key=lambda s: allowed_codes.index(s.code) if s.code in allowed_codes else 999
                )

                # Optional: reorder sequences for display
                for i, st in enumerate(ordered_states):
                    st.sequence = i

                rec.available_state_ids = [(6, 0, ordered_states.ids)]

    # @api.depends('job_card_state_code', 'current_user_id')
    # def _compute_available_state_ids(self):
    #     # Define state transitions based on job_card_state_code and user groups
    #     domain = []
    #     domain_backoffice = []
    #     domain_mobile = []
    #     domain_parts = []
    #
    #     code_search = self.env['project.task.type'].search([('code', '=','101')],limit = 1)
    #     for code in code_search:
    #         if code.back_office_user_code:
    #             domain_backoffice = code.back_office_user_code.split(",")
    #         if code.mobile_user_code:
    #             domain_mobile = code.mobile_user_code.split(",")
    #         if code.parts_user_code:
    #             domain_parts = code.parts_user_code.split(",")
    #
    #
    #     state_transitions = {
    #
    #         # for parallel run  only it will be commented on sep 26 2025
    #         # '101': {
    #         #     'machine_repair_management.group_job_card_back_office_user': ['101', '102','124'],
    #         #
    #         # },
    #
    #
    #          '101': {
    #             # 'machine_repair_management.group_job_card_back_office_user': ['101', '102','124'],
    #             'machine_repair_management.group_job_card_back_office_user': domain_backoffice,
    #
    #             'machine_repair_management.group_technical_allocation_user':domain_backoffice
    #             # 'machine_repair_management.group_technical_allocation_user':['121','124','125','126'],
    #
    #
    #         },
    #         '102': {
    #              'machine_repair_management.group_job_card_mobile_user': ['102', '103', '104'],
    #             # 'machine_repair_management.group_job_card_back_office_user': ['102', '103', '104','124'],
    #             # 'machine_repair_management.group_technical_allocation_user':['102','121','124','125','126'],
    #              'machine_repair_management.group_job_card_back_office_user':['101','102','129', '131','114','127', '128',  '121','122', '107', '125','124','112','126'],
    #
    #
    #         },
    #         ## After technician Accepted client asked to change the status as follows on Sep 23-2025
    #             # • Once the technician accepts a job card, the following status options should become available:
    #             # ◦ Customer Accepted
    #             # ◦ Reschedule
    #             # ◦ Cancellation
    #         '103': {
    #
    #             'machine_repair_management.group_job_card_mobile_user': ['103', '108', '107','124'],
    #             # 'machine_repair_management.group_job_card_mobile_user': ['103', '104', '105', '106', '107', '108','124'],
    #             'machine_repair_management.group_job_card_back_office_user': ['105', '106', '107', '108'],
    #
    #         },
    #         '104': {
    #             'machine_repair_management.group_job_card_mobile_user': ['104'],
    #             # 'machine_repair_management.group_job_card_mobile_user': ['104', '107'],
    #
    #             'machine_repair_management.group_job_card_back_office_user': ['104','107'],
    #         },
    #         '105': {
    #             'machine_repair_management.group_job_card_mobile_user': ['105', '107'],
    #             'machine_repair_management.group_job_card_back_office_user': ['107'],
    #         },
    #         '106': {
    #             'machine_repair_management.group_job_card_mobile_user': ['106', '107'],
    #             'machine_repair_management.group_job_card_back_office_user': ['107'],
    #         },
    #         '107': {
    #
    #             'machine_repair_management.group_job_card_mobile_user': ['107'],
    #              'machine_repair_management.group_job_card_back_office_user':['101','102','129', '131','114','127', '128',  '121','122', '107', '125','124','112','126'],
    #
    #             # 'machine_repair_management.group_job_card_back_office_user': ['103', '104'],
    #         },
    #         '108': {
    #             'machine_repair_management.group_job_card_mobile_user': ['108', '109'],
    #         },
    #         '109': {
    #             'machine_repair_management.group_job_card_mobile_user': ['109', '110','107'],
    #         },
    #         '110': {
    #             'machine_repair_management.group_job_card_mobile_user': ['110', '111'],
    #
    #
    #         },
    #      # Dated on Sep 23-2025
    #         #      After the technician completes Warranty Verification, update the job card status to one of the following:
    #         # ◦ Inspection Started
    #         # ◦ Cancelled – Inspection Charge Rejected by Customer
    #         # '111': {
    #         #     'machine_repair_management.group_job_card_mobile_user': ['111', '112', '113', '114', '115'],
    #         # },
    #         '111': {
    #             'machine_repair_management.group_job_card_mobile_user': ['111', '113', '112'],
    #         },
    #         '112': {
    #             'machine_repair_management.group_job_card_mobile_user': ['112', '124'],
    #             # 'machine_repair_management.group_job_card_back_office_user': ['112', '124', '126'],
    #             'machine_repair_management.group_job_card_back_office_user':['101','102','129', '131','114','127', '128',  '121','122', '107', '125','124','112','126'],
    #
    #         },
    #
    #         # Dated on Sep 23-2025
    #         # When the technician clicks “Inspection Started”, the system should display a list of status options:
    #         # ◦ Ready to Invoice
    #         # ◦ On Hold – Sp Req
    #         # ◦ Customer Need Quote
    #         # ◦ Unit Pull Out
    #         # ◦ Customer Reject Service
    #         # ◦ Payment Refused
    #         # ◦ Request – Revisit
    #         # • Make Symptoms , Defects, Service tabs are mandatory.
    #         # '113': {
    #         #     'machine_repair_management.group_job_card_mobile_user': ['113', '121', '114', '125'],
    #         # },
    #         '113': {
    #             'machine_repair_management.group_job_card_mobile_user': ['113', '125','121', '129','117','130','116','107'],
    #         },
    #
    #         '114': {
    #             'machine_repair_management.group_job_card_mobile_user': ['114', '127', '128'],
    #             # 'machine_repair_management.group_job_card_back_office_user': ['114', '115', '116', '124', '127', '128'],
    #             'machine_repair_management.group_job_card_back_office_user':['101','102','129', '131','114','127', '128',  '121','122', '107', '125','124','112','126'],
    #
    #         },
    #         '115': {
    #             'machine_repair_management.group_job_card_mobile_user': ['115', '117', '121', '125'],
    #             'machine_repair_management.group_job_card_back_office_user': ['115', '117', '121', '125'],
    #
    #         },
    #         '116': {
    #             'machine_repair_management.group_job_card_mobile_user': ['116'],
    #             # 'machine_repair_management.group_job_card_mobile_user': ['116', '107'],
    #
    #             'machine_repair_management.group_job_card_back_office_user': ['116', '107', '124'],
    #
    #         },
    #         '117': {
    #             'machine_repair_management.group_job_card_mobile_user': ['117'],
    #             # 'machine_repair_management.group_job_card_mobile_user': ['117', '121'],
    #
    #             'machine_repair_management.group_job_card_back_office_user': ['117','107', '123', '124'],
    #         },
    #         '121': {
    #             'machine_repair_management.group_job_card_mobile_user': ['121'],
    #             # 'machine_repair_management.group_job_card_mobile_user': ['121', '123', '124'],
    #              'machine_repair_management.group_job_card_back_office_user':['101','102','129', '131','114','127', '128',  '121','122', '107', '125','124','112','126'],
    #
    #             # 'machine_repair_management.group_job_card_back_office_user': ['121', '122', '107', '123', '125'],
    #             'machine_repair_management.group_parts_user':['121', '122'],
    #             # 'machine_repair_management.group_technical_allocation_user':['121','124','125','126'],
    #
    #         },
    #         '122': {
    #             'machine_repair_management.group_job_card_mobile_user': ['122', '123', '107'],
    #             # 'machine_repair_management.group_technical_allocation_user': ['122', '124', '125','126'],
    #              'machine_repair_management.group_job_card_back_office_user':['101','102','129', '131','114','127', '128',  '121','122', '107', '125','124','112','126'],
    #
    #
    #         },
    #         '123': {
    #             'machine_repair_management.group_job_card_mobile_user': ['123', '125', '107'],
    #             'machine_repair_management.group_job_card_back_office_user': ['123', '125'],
    #
    #         },
    #          '124': {
    #             'machine_repair_management.group_job_card_back_office_user':['101','102','129', '131','114','127', '128',  '121','122', '107', '125','124','112','126'],
    #
    #         },
    #
    #
    #         '125': {
    #             'machine_repair_management.group_job_card_mobile_user': ['125','126','124'],
    #             'machine_repair_management.group_job_card_back_office_user':['101','102','129', '131','114','127', '128',  '121','122', '107', '125','124','112','126'],
    #
    #             # 'machine_repair_management.group_job_card_back_office_user': ['125', '124', '126'],
    #         },
    #          '126': {
    #             'machine_repair_management.group_job_card_back_office_user':['101','102','129', '131','114','127', '128',  '121','122', '107', '125','124','112','126'],
    #
    #             # 'machine_repair_management.group_job_card_back_office_user': ['125', '124', '126'],
    #         },
    #
    #          '127': {
    #             'machine_repair_management.group_job_card_mobile_user': ['127'],
    #             # 'machine_repair_management.group_job_card_mobile_user': ['127', '115', '116','121'],
    #
    #             # 'machine_repair_management.group_job_card_back_office_user': ['127', '115', '116'],
    #             'machine_repair_management.group_job_card_back_office_user':['101','102','129', '131','114','127', '128',  '121','122', '107', '125','124','112','126'],
    #
    #         },
    #           '128': {
    #             'machine_repair_management.group_job_card_mobile_user': ['128', '124', '107'],
    #             # 'machine_repair_management.group_job_card_back_office_user': ['128', '124', '107'],
    #             'machine_repair_management.group_job_card_back_office_user':['101','102','129', '131','114','127', '128',  '121','122', '107', '125','124','112','126'],
    #
    #         },
    #           '129':{
    #
    #               'machine_repair_management.group_parts_user':['129', '131'],
    #               'machine_repair_management.group_job_card_mobile_user': ['129'],
    #              'machine_repair_management.group_job_card_back_office_user':['101','102','129', '131','114','127', '128',  '121','122', '107', '125','124','112','126'],
    #
    #               },
    #
    #           '130':{
    #
    #               'machine_repair_management.group_job_card_mobile_user': ['130'],
    #
    #               },
    #
    #           '131':{
    #                 # 'machine_repair_management.group_technical_allocation_user':['131','107','124'],
    #                 'machine_repair_management.group_job_card_back_office_user':['101','102','129', '131','114','127', '128',  '121','122', '107', '125','124','112','126'],
    #
    #               }
    #
    #     }
    #
    #     # Fetch all project.task.type records at once
    #     # type_by_code = self._get_task_type_by_code()
    #     ''' this is currently worked correctly
    #     task_types = self.env['project.task.type'].search([('code', 'in', list(set(code for transitions in state_transitions.values() for codes in transitions.values() for code in codes)))])
    #     type_by_code = {t.code: t.id for t in task_types}
    #     '''
    #
    #     all_codes = list(set(code for transitions in state_transitions.values() for codes in transitions.values() for code in codes))
    #     task_types = self.env['project.task.type'].search([('code', 'in', all_codes)])
    #     type_by_code = {t.code: t.id for t in task_types}
    #
    #
    #     # Check user groups once
    #     is_back_office = self.env.user.has_group('machine_repair_management.group_job_card_back_office_user')
    #     is_mobile = self.env.user.has_group('machine_repair_management.group_job_card_mobile_user')
    #     is_parts_officer = self.env.user.has_group('machine_repair_management.group_parts_user')
    #     is_technical_officer = self.env.user.has_group('machine_repair_management.group_technical_allocation_user')
    #
    #     # Process each record
    #     for rec in self:
    #         rec.available_state_ids = [(5,)]  # Clear existing states
    #         if not rec.job_card_state_code or rec.job_card_state_code not in state_transitions:
    #             continue
    #         # if not rec.job_card_state_code:
    #         #     continue
    #
    #         # Get allowed state codes based on user groups
    #         '''If second time visit technician is changed then technician need not once again warranty verification is not checked'''
    #         if rec.job_card_state_code == '110' and rec.second_visit_technician_bool:
    #             current_transitions = copy.deepcopy(state_transitions)
    #             current_transitions['110']['machine_repair_management.group_job_card_mobile_user'] = ['110','125']
    #         else:
    #             current_transitions = state_transitions
    #
    #         allowed_codes = []
    #
    #         # Find allowed states based on user group
    #         if rec.job_card_state_code in current_transitions:
    #             if (
    #                 is_parts_officer
    #                 and rec.current_user_id.has_group('machine_repair_management.group_parts_user')
    #                 and 'machine_repair_management.group_parts_user' in current_transitions[rec.job_card_state_code]
    #             ):
    #                 allowed_codes.extend(current_transitions[rec.job_card_state_code]['machine_repair_management.group_parts_user'])
    #
    #             elif (
    #                 is_technical_officer
    #                 and rec.current_user_id.has_group('machine_repair_management.group_technical_allocation_user')
    #                 and 'machine_repair_management.group_technical_allocation_user' in current_transitions[rec.job_card_state_code]
    #             ):
    #                 allowed_codes.extend(current_transitions[rec.job_card_state_code]['machine_repair_management.group_technical_allocation_user'])
    #
    #             else:
    #                 if (
    #                     is_back_office
    #                     and rec.current_user_id.has_group('machine_repair_management.group_job_card_back_office_user')
    #                     and 'machine_repair_management.group_job_card_back_office_user' in current_transitions[rec.job_card_state_code]
    #                 ):
    #                     allowed_codes.extend(current_transitions[rec.job_card_state_code]['machine_repair_management.group_job_card_back_office_user'])
    #
    #                 elif (
    #                     is_mobile
    #                     and rec.current_user_id.has_group('machine_repair_management.group_job_card_mobile_user')
    #                     and 'machine_repair_management.group_job_card_mobile_user' in current_transitions[rec.job_card_state_code]
    #                 ):
    #                     allowed_codes.extend(current_transitions[rec.job_card_state_code]['machine_repair_management.group_job_card_mobile_user'])
    #
    #         ##### working code commented on Oct 24 due to second time visit they don't ask again warranty verification
    #         # allowed_codes = []
    #         # if rec.job_card_state_code in state_transitions:
    #         #
    #         #     if is_parts_officer and rec.current_user_id.has_group('machine_repair_management.group_parts_user') and 'machine_repair_management.group_parts_user' in state_transitions[rec.job_card_state_code]:
    #         #         # If the user is a parts user, only use parts user states
    #         #         allowed_codes.extend(state_transitions[rec.job_card_state_code]['machine_repair_management.group_parts_user'])
    #         #     elif is_technical_officer and rec.current_user_id.has_group('machine_repair_management.group_technical_allocation_user') and 'machine_repair_management.group_technical_allocation_user' in state_transitions[rec.job_card_state_code]:
    #         #         allowed_codes.extend(state_transitions[rec.job_card_state_code]['machine_repair_management.group_technical_allocation_user'])
    #         #     else:
    #         #         #     # Normal transition logic for other cases
    #         #         if is_back_office and rec.current_user_id.has_group('machine_repair_management.group_job_card_back_office_user') and 'machine_repair_management.group_job_card_back_office_user' in state_transitions[rec.job_card_state_code]:
    #         #             allowed_codes.extend(state_transitions[rec.job_card_state_code]['machine_repair_management.group_job_card_back_office_user'])
    #         #         elif is_mobile and rec.current_user_id.has_group('machine_repair_management.group_job_card_mobile_user') and 'machine_repair_management.group_job_card_mobile_user' in state_transitions[rec.job_card_state_code]:
    #         #             allowed_codes.extend(state_transitions[rec.job_card_state_code]['machine_repair_management.group_job_card_mobile_user'])
    #         #
    #
    #         # Map state codes to IDs
    #         ''' currently working commented on Oct-08-2025 for ordered sequence
    #         state_ids = [type_by_code[code] for code in set(allowed_codes) if code in type_by_code]
    #         if state_ids:
    #             rec.available_state_ids = [(6, 0, state_ids)]
    #
    #         '''
    #         state_ids = []
    #
    #         for code in allowed_codes:
    #             if code in type_by_code:
    #                 state_ids.append(type_by_code[code])
    #         if state_ids:
    #             ordered_records = self.env['project.task.type'].browse(state_ids)
    #
    #             ordered_records = ordered_records.sorted(
    #                 key=lambda r: allowed_codes.index(r.code) if r.code in allowed_codes else 999
    #             )
    #
    #             for idx, rec_type in enumerate(ordered_records):
    #                 rec_type.sequence = idx  # Forces the display order in the statusbar
    #
    #             rec.available_state_ids = [(6, 0, ordered_records.ids)]

    # def read(self, fields=None, load='_classic_read'):
    #     res = super(ProjectTask, self).read(fields, load)
    #     for rec in self:
    #         rec._compute_available_state_ids()
    #     return res
    #
    # @api.depends('job_card_state_code')
    # def _compute_available_state_ids(self):
    #     for rec in self:
    #         rec.available_state_ids = False
    #
    #         ''' New  State '''
    #         if rec.job_card_state_code == '101':
    #
    #
    #             if self.env.user.has_group('machine_repair_management.group_job_card_back_office_user'):
    #                 state_lst = []
    #                 job_state_back_office = self.env['project.task.type'].search([('code','=',('102'))])
    #                 for job in job_state_back_office:
    #                     state_lst.append(job.id)
    #                     rec.job_state = self.job_state
    #                 if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                     if state_lst:
    #                         rec.available_state_ids =[(6,0,state_lst)]
    #                     else:
    #                         rec.available_state_ids = [(5,)]
    #
    #                 else:
    #                     rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #
    #             ''' Scheduled (Technician Assigned) State '''
    #         elif rec.job_card_state_code == '102':
    #             # if not rec.team_id :
    #             #     raise ValidationError("Please enter Team Leader name in the Job card")
    #             #
    #             # if not rec.technician_id:
    #             #
    #             #     raise ValidationError("Please Enter Technician Name ")
    #
    #             if self.env.user.has_group('machine_repair_management.group_job_card_back_office_user'):
    #                 state_lst = []
    #                 job_state_back_office = self.env['project.task.type'].search([('code','in',('102','103','104'))])
    #                 for job in job_state_back_office:
    #                     state_lst.append(job.id)
    #                     rec.job_state = self.job_state
    #                 if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                     if state_lst:
    #                         rec.available_state_ids =[(6,0,state_lst)]
    #                     else:
    #                         rec.available_state_ids = [(5,)]
    #
    #                 else:
    #                     rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #             ''' Technician Accepted State'''
    #         elif rec.job_card_state_code =='103':
    #             # rec.technician_accepted_date = fields.Datetime.now()
    #             if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                 state_lst = []
    #                 job_state_update = self.env['project.task.type'].search([('code', 'in', ['103', '104', '105', '106', '107','108'])])
    #                 for job_state in job_state_update:
    #                     state_lst.append(job_state.id)
    #                     rec.job_state = self.job_state
    #                     print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                 if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                     if state_lst:
    #                         rec.available_state_ids = [(6, 0, state_lst)]
    #                     else:
    #                         rec.available_state_ids = [(5,)]
    #                 else:
    #                     rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #             elif self.env.user.has_group('machine_repair_management.group_job_card_back_office_user'):
    #                 state_lst = []
    #                 job_state_back_office = self.env['project.task.type'].search([('code','in',('105','106','107','108'))])
    #                 for job in job_state_back_office:
    #                     state_lst.append(job.id)
    #                     rec.job_state = self.job_state
    #                 if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                     if state_lst:
    #                         rec.available_state_ids =[(6,0,state_lst)]
    #                     else:
    #                         rec.available_state_ids = [(5,)]
    #
    #                 else:
    #                     rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #                 ''' Technician Rejected state'''
    #         elif rec.job_card_state_code == '104':
    #             # rec.technician_rejected_date = fields.Datetime.now()
    #             if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                 state_lst = []
    #                 job_state_update = self.env['project.task.type'].search([('code', 'in', ['104', '107'])])
    #                 for job_state in job_state_update:
    #                     state_lst.append(job_state.id)
    #                     rec.job_state = self.job_state
    #                     print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                 if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                     if state_lst:
    #                         rec.available_state_ids = [(6, 0, state_lst)]
    #                     else:
    #                         rec.available_state_ids = [(5,)]
    #                 else:
    #                     rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #
    #             elif self.env.user.has_group('machine_repair_management.group_job_card_back_office_user'):
    #                 state_lst = []
    #                 job_state_back_office = self.env['project.task.type'].search([('code','=',('107'))])
    #                 for job in job_state_back_office:
    #                     state_lst.append(job.id)
    #                     rec.job_state = self.job_state
    #                 if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #
    #                     if state_lst:
    #                         rec.available_state_ids =[(6,0,state_lst)]
    #                     else:
    #                         rec.available_state_ids = [(5,)]
    #
    #                 else:
    #                     rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #             ''' Failed to attend call (Customer not answered) '''
    #         elif rec.job_card_state_code == '105':
    #             if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                 state_lst = []
    #                 job_state_update = self.env['project.task.type'].search([('code', 'in', ['105', '107'])])
    #                 for job_state in job_state_update:
    #                     state_lst.append(job_state.id)
    #                     rec.job_state = self.job_state
    #                     print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                 if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                     if state_lst:
    #                         rec.available_state_ids = [(6, 0, state_lst)]
    #                     else:
    #                         rec.available_state_ids = [(5,)]
    #                 else:
    #                     rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #
    #             elif self.env.user.has_group('machine_repair_management.group_job_card_back_office_user'):
    #                 state_lst = []
    #                 job_state_update = self.env['project.task.type'].search([('code', 'in', ['107'])])
    #                 for job_state in job_state_update:
    #                     state_lst.append(job_state.id)
    #                     rec.job_state = self.job_state
    #                     print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                 if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                     if state_lst:
    #                         rec.available_state_ids = [(6, 0, state_lst)]
    #                     else:
    #                         rec.available_state_ids = [(5,)]
    #                 else:
    #                     rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #                     ''' Out of City '''
    #         elif rec.job_card_state_code == '106':
    #             if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                 state_lst = []
    #                 job_state_update = self.env['project.task.type'].search([('code', 'in', ['106', '107'])])
    #                 for job_state in job_state_update:
    #                     state_lst.append(job_state.id)
    #                     rec.job_state = self.job_state
    #                     print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                 if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                     if state_lst:
    #                         rec.available_state_ids = [(6, 0, state_lst)]
    #                     else:
    #                         rec.available_state_ids = [(5,)]
    #                 else:
    #                     rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #             elif self.env.user.has_group('machine_repair_management.group_job_card_back_office_user'):
    #                 state_lst = []
    #                 job_state_update = self.env['project.task.type'].search([('code', '=', ['107'])])
    #                 for job_state in job_state_update:
    #                     state_lst.append(job_state.id)
    #                     rec.job_state = self.job_state
    #                     print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                 if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                     if state_lst:
    #                         rec.available_state_ids = [(6, 0, state_lst)]
    #                     else:
    #                         rec.available_state_ids = [(5,)]
    #                 else:
    #                     rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #
    #                     ''' Rescheduled State '''
    #         elif rec.job_card_state_code == '107':
    #             if self.env.user.has_group('machine_repair_management.group_job_card_back_office_user'):
    #                 state_lst = []
    #                 job_state_update = self.env['project.task.type'].search([('code', 'in', ['103', '104'])])
    #                 for job_state in job_state_update:
    #                     state_lst.append(job_state.id)
    #                     rec.job_state = self.job_state
    #                     print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                 if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                     if state_lst:
    #                         rec.available_state_ids = [(6, 0, state_lst)]
    #                     else:
    #                         rec.available_state_ids = [(5,)]
    #                 else:
    #                     rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #
    #
    #
    #                     ''' Customer Accepted State '''
    #         elif rec.job_card_state_code == '108':
    #             if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                 state_lst = []
    #                 job_state_update = self.env['project.task.type'].search([('code', 'in', ['103', '107', '108'])])
    #                 for job_state in job_state_update:
    #                     state_lst.append(job_state.id)
    #                     rec.job_state = self.job_state
    #                     print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                 if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                     if state_lst:
    #                         rec.available_state_ids = [(6, 0, state_lst)]
    #                     else:
    #                         rec.available_state_ids = [(5,)]
    #                 else:
    #                     rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #                     ''' Technician Started State '''
    #         elif rec.job_card_state_code =='109':
    #             # rec.technician_started_date = fields.Datetime.now()
    #             if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                 state_lst = []
    #                 job_state_update = self.env['project.task.type'].search([('code', 'in', ['107', '109', '110'])])
    #                 for job_state in job_state_update:
    #                     state_lst.append(job_state.id)
    #                     rec.job_state = self.job_state
    #                     print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                 if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                     if state_lst:
    #                         rec.available_state_ids = [(6, 0, state_lst)]
    #                     else:
    #                         rec.available_state_ids = [(5,)]
    #                 else:
    #                     rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #             ''' Technician Reached State '''
    #         elif rec.job_card_state_code == '110':
    #             # rec.technician_reached_date = fields.Datetime.now()
    #             if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                 state_lst = []
    #                 job_state_update = self.env['project.task.type'].search([('code', 'in', ['110', '111'])])
    #                 for job_state in job_state_update:
    #                     state_lst.append(job_state.id)
    #                     rec.job_state = self.job_state
    #                     print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                 if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                     if state_lst:
    #                         rec.available_state_ids = [(6, 0, state_lst)]
    #                     else:
    #                         rec.available_state_ids = [(5,)]
    #                 else:
    #                     rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #             ''' Warranty Verification State '''
    #         elif rec.job_card_state_code == '111':
    #             if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                 state_lst = []
    #                 job_state_update = self.env['project.task.type'].search([('code', 'in', ['111', '112', '113', '114'])])
    #                 for job_state in job_state_update:
    #                     state_lst.append(job_state.id)
    #                     rec.job_state = self.job_state
    #                     print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                 if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                     if state_lst:
    #                         rec.available_state_ids = [(6, 0, state_lst)]
    #                     else:
    #                         rec.available_state_ids = [(5,)]
    #                 else:
    #                     rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #             ''' Inspection Started State '''
    #         elif rec.job_card_state_code == '113':
    #             if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                 state_lst = []
    #                 job_state_update = self.env['project.task.type'].search([('code', 'in', ['113', '114', '125'])])
    #                 for job_state in job_state_update:
    #                     state_lst.append(job_state.id)
    #                     rec.job_state = self.job_state
    #                     print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                 if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                     if state_lst:
    #                         rec.available_state_ids = [(6, 0, state_lst)]
    #                     else:
    #                         rec.available_state_ids = [(5,)]
    #                 else:
    #                     rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #             ''' Quotation provided. Waiting customer approval State '''
    #         elif rec.job_card_state_code == '114':
    #             if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                 state_lst = []
    #                 job_state_update = self.env['project.task.type'].search([('code', 'in', ['114', '115', '116'])])
    #                 for job_state in job_state_update:
    #                     state_lst.append(job_state.id)
    #                     rec.job_state = self.job_state
    #                     print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                 if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                     if state_lst:
    #                         rec.available_state_ids = [(6, 0, state_lst)]
    #                     else:
    #                         rec.available_state_ids = [(5,)]
    #                 else:
    #                     rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #
    #
    #             elif self.env.user.has_group('machine_repair_management.group_job_card_back_office_user'):
    #                 state_lst = []
    #                 job_state_update = self.env['project.task.type'].search([('code', '=', ['124'])])
    #                 for job_state in job_state_update:
    #                     state_lst.append(job_state.id)
    #                     rec.job_state = self.job_state
    #                     print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                 if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                     if state_lst:
    #                         rec.available_state_ids = [(6, 0, state_lst)]
    #                     else:
    #                         rec.available_state_ids = [(5,)]
    #                 else:
    #                     rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #             ''' Job Started (In-progress) State '''
    #         elif rec.job_card_state_code == '115':
    #             # rec.job_started_date = fields.Datetime.now()
    #             if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                 state_lst = []
    #                 job_state_update = self.env['project.task.type'].search([('code', 'in', ['115', '117', '121'])])
    #                 for job_state in job_state_update:
    #                     state_lst.append(job_state.id)
    #                     rec.job_state = self.job_state
    #                     print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                 if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                     if state_lst:
    #                         rec.available_state_ids = [(6, 0, state_lst)]
    #                     else:
    #                         rec.available_state_ids = [(5,)]
    #                 else:
    #                     rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #
    #             ''' Payment Refused State '''
    #         elif rec.job_card_state_code =='116':
    #             # rec.job_started_date = fields.Datetime.now()
    #             if self.env.user.has_group('machine_repair_management.group_job_card_back_office_user'):
    #                 state_lst = []
    #                 job_state_update = self.env['project.task.type'].search([('code', 'in', ['107', '124'])])
    #                 for job_state in job_state_update:
    #                     state_lst.append(job_state.id)
    #                     rec.job_state = self.job_state
    #                     print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                 if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                     if state_lst:
    #                         rec.available_state_ids = [(6, 0, state_lst)]
    #                     else:
    #                         rec.available_state_ids = [(5,)]
    #                 else:
    #                     rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #
    #             ''' Unit Pull Out State '''
    #         elif rec.job_card_state_code == '117':
    #             if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                 state_lst = []
    #                 job_state_update = self.env['project.task.type'].search([('code', 'in', ['117', '121'])])
    #                 for job_state in job_state_update:
    #                     state_lst.append(job_state.id)
    #                     rec.job_state = self.job_state
    #                     print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                 if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                     if state_lst:
    #                         rec.available_state_ids = [(6, 0, state_lst)]
    #                     else:
    #                         rec.available_state_ids = [(5,)]
    #                 else:
    #                     rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #             elif self.env.user.has_group('machine_repair_management.group_job_card_back_office_user'):
    #                 state_lst = []
    #                 job_state_update = self.env['project.task.type'].search([('code', 'in', ['123', '124','107'])])
    #                 for job_state in job_state_update:
    #                     state_lst.append(job_state.id)
    #                     rec.job_state = self.job_state
    #                     print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                 if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                     if state_lst:
    #                         rec.available_state_ids = [(6, 0, state_lst)]
    #                     else:
    #                         rec.available_state_ids = [(5,)]
    #                 else:
    #                     rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #             ''' On Hold - Spare Parts Required State '''
    #         elif rec.job_card_state_code == '121':
    #             # rec.job_hold_date = fields.Datetime.now()
    #             if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                 state_lst = []
    #                 job_state_update = self.env['project.task.type'].search([('code', 'in', ['121', '123', '124'])])
    #                 for job_state in job_state_update:
    #                     state_lst.append(job_state.id)
    #                     rec.job_state = self.job_state
    #                     print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                 if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                     if state_lst:
    #                         rec.available_state_ids = [(6, 0, state_lst)]
    #                     else:
    #                         rec.available_state_ids = [(5,)]
    #                 else:
    #                     rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #
    #             elif self.env.user.has_group('machine_repair_management.group_job_card_back_office_user'):
    #                 state_lst = []
    #                 job_state_update = self.env['project.task.type'].search([('code', 'in', ['123', '107'])])
    #                 for job_state in job_state_update:
    #                     state_lst.append(job_state.id)
    #                     rec.job_state = self.job_state
    #                     print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                 if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                     if state_lst:
    #                         rec.available_state_ids = [(6, 0, state_lst)]
    #                     else:
    #                         rec.available_state_ids = [(5,)]
    #                 else:
    #                     rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #             ''' Parts Ready State '''
    #         elif rec.job_card_state_code in ('122','123'):
    #             # rec.job_resume_date = fields.Datetime.now()
    #
    #             if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                 state_lst = []
    #                 job_state_update = self.env['project.task.type'].search([('code', 'in', ['122', '123', '107'])])
    #                 for job_state in job_state_update:
    #                     state_lst.append(job_state.id)
    #                     rec.job_state = self.job_state
    #                     print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                 if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                     if state_lst:
    #                         rec.available_state_ids = [(6, 0, state_lst)]
    #                     else:
    #                         rec.available_state_ids = [(5,)]
    #                 else:
    #                     rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #             ''' Ready to Invoice (Complete) State '''
    #         elif rec.job_card_state_code == '125':
    #             # rec.closed_datetime = fields.Datetime.now()
    #             if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #                 state_lst = []
    #                 job_state_update = self.env['project.task.type'].search([('code', 'in', ['125', '126'])])
    #                 for job_state in job_state_update:
    #                     state_lst.append(job_state.id)
    #                     rec.job_state = self.job_state
    #                     print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                 if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                     if state_lst:
    #                         rec.available_state_ids = [(6, 0, state_lst)]
    #                     else:
    #                         rec.available_state_ids = [(5,)]
    #                 else:
    #                     rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False
    #
    #             elif self.env.user.has_group('machine_repair_management.group_job_card_back_office_user'):
    #                 state_lst = []
    #                 job_state_update = self.env['project.task.type'].search([('code', 'in', ['126'])])
    #                 for job_state in job_state_update:
    #                     state_lst.append(job_state.id)
    #                     rec.job_state = self.job_state
    #                     print("job_state,code", job_state.id, job_state.name, job_state.code)
    #                 if hasattr(rec, 'available_state_ids') and rec.available_state_ids:
    #                     if state_lst:
    #                         rec.available_state_ids = [(6, 0, state_lst)]
    #                     else:
    #                         rec.available_state_ids = [(5,)]
    #                 else:
    #                     rec.available_state_ids = [(6, 0, state_lst)] if state_lst else False

    # '''If the User selected the Parts Ready Job state then check all the parts should be ticked in the product consume parts/service by Vijaya Bhaskar on June-30-2025'''
    # if rec.job_card_state_code in ('122','126'):
    #     if rec.product_line_ids:
    #         for line in rec.product_line_ids:
    #             if line.product_id:
    #                 if not line.parts_reserved_bool:
    #                     raise ValidationError("Please check all the Products should be Reserved.This Product %s is not reserved" % line.product_id.display_name)
    #
    #             if line.on_hand_qty == 0.0:
    #                 raise ValidationError("Please Stock is not available.Please Contact Administrator")
    #
    #
    #
    #
    #     if not rec.product_line_ids:
    #         raise ValidationError("Please give any one of the Product in the product consume Part/services")
    #

    @api.depends('product_line_ids.under_warranty_bool', 'product_line_ids.price_unit', 'product_line_ids.tax_amount',
                 'product_line_ids.qty', 'inspection_charges_amount')
    def _compute_parts_total_amount(self):
        for rec in self:
            '''this code is commented by Vijaya bhaskar on July 15 2025  because the service type is also treated as storable product. so we add the service_type_bool in product.product'''
            rec.parts_total_amount = sum(
                line.price_unit * line.qty for line in rec.product_line_ids if not line.under_warranty_bool if
                not line.product_id.service_type_bool)
            rec.parts_vat_totamount = sum(
                line.tax_amount for line in rec.product_line_ids if not line.under_warranty_bool if
                not line.product_id.service_type_bool)
            # rec.parts_total_amount = sum(line.price_unit for line in rec.product_line_ids if not line.under_warranty_bool if line.product_id.type != 'service' )
            # rec.parts_vat_totamount = sum(line.tax_amount for line in rec.product_line_ids if not line.under_warranty_bool if line.product_id.type != 'service' )

            rec.parts_grand_total_amount = rec.parts_total_amount + rec.parts_vat_totamount

            rec.service_charge_amount = sum(
                line.price_unit * line.qty for line in rec.product_line_ids if not line.under_warranty_bool if
                line.product_id.service_type_bool)
            rec.service_vat_amount = sum(
                line.tax_amount for line in rec.product_line_ids if not line.under_warranty_bool if
                line.product_id.service_type_bool)
            rec.service_grand_total_amount = sum([rec.service_charge_amount, rec.service_vat_amount])

    # @api.depends('team_id', 'team_id.team_member_ids')
    # def _compute_available_user_ids(self):
    #     for rec in self:
    #         rec.available_user_ids = rec.team_id.team_member_ids

    @api.depends('team_id', 'team_id.support_team_line_ids')
    def _compute_available_user_ids(self):
        for rec in self:
            rec.available_user_ids = False
            team_lst = []
            if rec.team_id:
                if rec.team_id.support_team_line_ids:
                    for line in rec.team_id.support_team_line_ids:
                        team_lst.append(line.support_team_user_id.id)
                        # if line.is_default_team_member:
                        rec.available_user_ids = team_lst

    @api.onchange('team_id', 'technician_id')
    def _onchange_team_id(self):
        for rec in self:
            if rec.team_id:
                available_ids = rec.available_user_ids.ids
                # if not rec.technician_id:
                default_line = rec.team_id.support_team_line_ids.filtered(lambda l: l.is_default_team_member)
                if default_line and default_line.support_team_user_id.id in available_ids:
                    rec.technician_id = default_line.support_team_user_id.id
                elif available_ids:
                    rec.technician_id = available_ids[0]  # fallback to first available

                # rec.service_request_id.team_id = rec.team_id.id
                # rec.service_request_id.user_id =  rec.technician_id.id
                # scheduled_state = self.env['project.task.type'].search(
                #                     [('code','=','102')],
                #                     limit=1
                #                 )
                #
                #
                # if scheduled_state:
                #     rec.job_state = scheduled_state
                # rec._onchange_job_card_state_status()
                # rec.write({'job_card_state_code':'102'})

                ''' for create the timesheet'''
                #     val_lst = [(5,0,0)]
            #     vals = {
            #         'date' : self.service_created_datetime.date(),
            #         'user_id' : self.technician_id.id,
            #         'project_id':self.project_id.id,
            #         'company_id':self.company_id.id,
            #         'name': self.name,
            #         'unit_amount':0.0,
            #         }
            #
            #     val_lst.append((0,0,vals))
            #
            # rec.timesheet_line_ids = val_lst

            # if rec.technician_id:
            #     rec.user_ids = rec.technician_id.ids

    # @api.onchange('team_id', 'technician_id')
    # def _onchange_team_id(self):
    #     for rec in self:
    #         if rec.team_id:
    #             available_ids = rec.available_user_ids.ids
    #             default_line = rec.team_id.support_team_line_ids.filtered(lambda l: l.is_default_team_member)
    #             if default_line and default_line.support_team_user_id.id in available_ids:
    #                 rec.technician_id = default_line.support_team_user_id.id
    #             elif available_ids:
    #                 rec.technician_id = available_ids[0]
    #             rec.service_request_id.team_id = rec.team_id.id
    #             rec.service_request_id.user_id = rec.technician_id.id
    #

    @api.onchange('user_ids')
    def _onchange_user_ids(self):
        for rec in self:
            if rec.user_ids:
                rec.technician_id = rec.user_ids.id

    @api.model
    def _get_job_state_domain(self):
        domain = []
        if self.project_id:
            project = self.env['project.project'].browse(self.project_id.id)
            if project.exists():
                domain.append(('project_ids', '=', project.id))

        user = self.env.user

        if user.has_group('machine_repair_management.group_job_card_back_office_user'):
            domain.append(('back_office_user', '=', True))

        elif user.has_group('machine_repair_management.group_job_card_mobile_user'):
            domain.append(('mobile_user', '=', True))
        # elif user.has_group('machine_repair_management.group_service_request_admin_user'):
        #     domain.append(('back_office_user', '=', True))
        #     domain.append(('mobile_user', '=', True))

        return domain

    @api.onchange('job_state')
    def _onchange_job_state(self):
        if self.job_state and not self.job_state.exists():
            self.job_state = False

            # print("........jobssssssssssssssssssssssssst",self.job_state,self.job_state.code,self.job_state.name)
        if self.job_state.code == '126':
            if self.env['ir.config_parameter'].sudo().get_param(
                    'machine_repair_management.negative_stock_allow') == 'True':

                for line in self.product_line_ids:
                    if line.on_hand_qty == 0.0:
                        # _logger.warning("Stock not available for product %s on Job Card %s", line.product_id.display_name, self.name)

                        # message = "Stock not available for the following products:\n• " + "\n• ".join(line.product_id.display_name)
                        #
                        # wizard = self.env['negative.stock.warning.wizard'].create({
                        #     'message': message,
                        #     'job_card_id': self.id,
                        # })
                        #

                        # return {
                        #     'name': 'Stock Warning',
                        #     'type': 'ir.actions.act_window',
                        #     'res_model': 'negative.stock.warning.wizard',
                        #     # 'res_id': wizard.id,
                        #     'view_mode': 'form',
                        #     'target': 'new',
                        #     'context': self.env.context,
                        # }
                        return {
                            'warning': {
                                'title': "Warning",
                                'message': "Stock not available for product %s" % line.product_id.display_name,
                            }
                        }
                        # if self.job_state.code == '124':
        #     return {
        #
        #     'type':'ir.actions.act_window',
        #     'res_model':'cancelled.reason.wizard',
        #     'name' : 'Cancelled Reason',
        #     'view_mode':'form',
        #     'views': [(False, 'form')],
        #     'target': 'new',
        #     'context': {
        #         'default_job_card_id': self.id,
        #     },
        #
        #     }

        # self.cancelled_reason_button()

    def _send_notification_to_supervisior(self):
        work_center = self.technician_id.default_work_center_id
        finance_users = self.env['res.users'].search([
            ('default_work_center_id', '=', work_center.id),
            ('groups_id', 'in', self.env.ref('machine_repair_management.group_technical_allocation_user').id)
        ])

        # if finance_users and rec.technician_id.partner_id:
        #     technician_user = rec.technician_id
        #     technician_partner = technician_user.partner_id
        # odoo_bot = self.env.ref('base.partner_root')

        odoo_bot = self.env.user.partner_id

        # Combine partner IDs into a single flat list
        for user in finance_users:
            if user.partner_id:
                # Find or create a private channel between OdooBot and the user
                channel_name = f"{odoo_bot.name}, {user.name}"
                channel = self.env['discuss.channel'].search([
                    ('name', 'ilike', channel_name),
                    ('channel_type', '=', 'chat')
                ], limit=1)
                if not channel:
                    channel = self.env['discuss.channel'].create({
                        'name': channel_name,
                        'channel_type': 'chat',
                        # 'public': 'private',
                        'channel_partner_ids': [(4, user.partner_id.id)]
                    })

                # Post the message
                message_body = (f"Technician {self.technician_id.name} has rescheduled the  "
                                f"Job Card {self.name}."
                                )
                channel.message_post(
                    body=message_body,
                    subject='Job Card State Update',
                    message_type='notification',
                    subtype_xmlid='mail.mt_comment',
                    # author_id=odoo_bot.id,
                )

    def _send_notification_to_technician(self):
        work_center = self.technician_id.default_work_center_id

        # Fetch finance users from the group
        group_id = self.env.ref('machine_repair_management.group_job_card_mobile_user').id
        finance_users = self.env['res.users'].search([('id', '=', self.technician_id.id)])

        # OdooBot as sender
        # odoo_bot = self.env.ref('base.partner_root')

        odoo_bot = self.env.user.partner_id

        for user in finance_users:
            if user.partner_id:

                # Create or fetch private chat channel
                channel_name = f"{odoo_bot.name}, {user.name}"
                channel = self.env['discuss.channel'].search([
                    ('name', 'ilike', channel_name),
                    ('channel_type', '=', 'chat')
                ], limit=1)
                if not channel:
                    channel = self.env['discuss.channel'].create({
                        'name': channel_name,
                        'channel_type': 'chat',
                        # 'public': 'private',
                        'channel_partner_ids': [(4, user.partner_id.id)]
                    })

                # Post message
                if self.job_card_state_code == '124':
                    message_body = f'Technician {self.technician_id.name} has put Job Card {self.name} on Cancelled.'
                    channel.message_post(
                        body=message_body,
                        subject='Job Card State Update',
                        message_type='notification',
                        subtype_xmlid='mail.mt_comment',
                        author_id=odoo_bot.id
                    )

    @api.model
    def create(self, vals):
        if vals.get('job_state'):
            state = self.env['project.task.type'].sudo().browse(vals['job_state'])
            if not state.exists():
                vals['job_state'] = False
        return super().create(vals)

    def write(self, vals):
        # if self.env.context.get('skip_state_validation'):
        #     return super().write(vals)
        #
        is_minimal_update = (
                len(vals) == 0 or
                all(field in ['message_main_attachment_id', 'message_ids', 'activity_ids', 'write_date',
                              '__last_update'] for field in vals.keys())
        )

        if is_minimal_update or self.env.context.get('creating'):
            return super().write(vals)

        if self.env.context.get('skip_state_validation'):
            return super().write(vals)

        warnings = []
        warning_needed = False
        state_changing_to_124 = False

        for rec in self:
            # Take new state code if being updated, otherwise existing
            state_code = vals.get('job_card_state_code') or rec.job_card_state_code
            engineer_comments = vals.get('engineer_comments') or rec.engineer_comments
            team_id = vals.get('team_id') or rec.team_id.id

            def is_state_changing_to(target_code):
                return ('job_state' in vals or 'job_card_state_code' in vals) and (
                        (vals.get('job_card_state_code') == target_code) or
                        (not vals.get('job_card_state_code') and vals.get('job_state') and
                         self.env['project.task.type'].browse(vals['job_state']).code == target_code)
                )

            # Check if state is being changed to specific codes
            state_changing_to_102 = is_state_changing_to('102')
            state_changing_to_107 = is_state_changing_to('107')

            state_changing_to_111 = is_state_changing_to('111')
            state_changing_to_112 = is_state_changing_to('112')

            state_changing_to_113 = is_state_changing_to('113')
            state_changing_to_115 = is_state_changing_to('115')
            state_changing_to_116 = is_state_changing_to('116')

            state_changing_to_117 = is_state_changing_to('117')
            state_changing_to_121 = is_state_changing_to('121')

            state_changing_to_122 = is_state_changing_to('122')
            state_changing_to_124 = is_state_changing_to('124')
            state_changing_to_125 = is_state_changing_to('125')
            state_changing_to_126 = is_state_changing_to('126')

            state_changing_to_128 = is_state_changing_to('128')
            state_changing_to_129 = is_state_changing_to('129')
            state_changing_to_130 = is_state_changing_to('130')

            if state_changing_to_102:
                if not team_id:
                    raise ValidationError(
                        _("Please assign the technician to this Job Card %s " % rec.name)
                    )

            if state_changing_to_124:
                ''' Engineer comments are commented due to not need during closed Job card
                if not engineer_comments:
                    raise ValidationError(
                        _("Please enter Engineer Comments before moving Job Card %s") % rec.name
                    )
                '''
                # self = self.with_context(open_cancelled_wizard=True)
                # if not self.cancel_button_wizard_bool:
                # raise UserError(_("Please Click the Cancel Job Card Button in mobile"))
                # return rec.cancelled_reason_button_mobile()
                cancellation_reason = vals.get('cancellation_reason_id') or rec.cancellation_reason_id

                # if not cancellation_reason:
                #     return rec.cancelled_reason_button_mobile()

                # raise ValidationError(_("Please Select any one Cancellation Reason before Cancel the Job Card."))

            if state_changing_to_122:
                if not rec.product_line_ids and not vals.get("product_line_ids"):
                    raise ValidationError(
                        _("Please give at least one Product in the product consume Part/services")
                    )

                for line in rec.product_line_ids:
                    if line.product_id:
                        if not line.parts_reserved_bool:
                            raise ValidationError(
                                _("Product %s is not reserved. Please reserve all products before proceeding." % line.product_id.display_name)

                            )
                    if line.on_hand_qty == 0.0:
                        raise ValidationError(
                            _("Stock is not available for Product %s. Please contact Administrator." % line.product_id.display_name)

                        )

                # Inspection charges check
                if rec.inspection_charges_bool and rec.inspection_charges_amount > 0:
                    if not any(
                            l.product_id and l.product_id.service_type_bool
                            for l in rec.product_line_ids
                    ):
                        raise ValidationError(
                            _("Please enter service charge amount in the product line")
                        )

            if state_changing_to_125:
                product_id = vals.get('product_id') or rec.product_id.id
                project_related_amc_bool = vals.get('project_related_amc_bool') or rec.project_related_amc_bool
                if not product_id and not project_related_amc_bool:
                    raise ValidationError(_("Please enter Model No. in the Job card"))
                product_slno = vals.get('product_slno') or rec.product_slno
                if not product_slno:
                    raise ValidationError(_("Please enter Serial Number in the Job card"))

                purchase_invoice_no = vals.get('purchase_invoice_no') or rec.purchase_invoice_no
                if rec.warranty and not purchase_invoice_no:
                    raise ValidationError(_("Please enter Purchase Invoice No in the Job card"))

                purchase_date = vals.get('purchase_date') or rec.purchase_date
                if rec.warranty and not purchase_date:
                    raise ValidationError(_("Please enter Purchase date in the Job card"))

                service_warranty_id = vals.get('service_warranty_id') or rec.service_warranty_id.id
                if not service_warranty_id:
                    raise ValidationError(_("Please select any one Service Warranty in the Job card"))

                signature = vals.get('signature') or rec.signature
                if not signature:
                    raise ValidationError(_("Please enter Customer Signature in the Job card"))

            if state_changing_to_126:

                '''  Control Card no should be hide as per client request on NOv 13
                control_card_no = vals.get('control_card_no') or rec.control_card_no
                if not control_card_no:
                    raise ValidationError(_("Please enter 'Control Card No' in the Job card."))
                '''
                closed_datetime = vals.get('closed_datetime') or rec.closed_datetime
                if not closed_datetime:
                    raise ValidationError(_("Please enter Completed Date & Time in the Job card"))

                # if closed_datetime:
                #     if rec.planned_date_begin and closed_datetime:
                #         if rec.planned_date_begin > closed_datetime:
                #             raise ValidationError('Completed Date & Time is always greater than Appt Start Date & Time')
                #
                if closed_datetime:
                    planned_dt = rec.planned_date_begin
                    closed_dt = fields.Datetime.from_string(closed_datetime) if isinstance(closed_datetime,
                                                                                           str) else closed_datetime

                    if planned_dt and closed_dt:
                        if planned_dt > closed_dt:
                            raise ValidationError(
                                _('Completed Date & Time is always greater than Appt Start Date & Time'))

                product_id = vals.get('product_id') or rec.product_id.id
                project_related_amc_bool = vals.get('project_related_amc_bool') or rec.project_related_amc_bool

                if not product_id and not project_related_amc_bool:
                    raise ValidationError(_("Please enter Model No. in the Job card"))

                purchase_invoice_no = vals.get('purchase_invoice_no') or rec.purchase_invoice_no
                if rec.warranty and not purchase_invoice_no:
                    raise ValidationError(_("Please enter Purchase Invoice No"))

                purchase_date = vals.get('purchase_date') or rec.purchase_date
                if rec.warranty and not purchase_date:
                    raise ValidationError(_("Please enter Purchase date in the Job card"))

                service_warranty_id = vals.get('service_warranty_id') or rec.service_warranty_id.id
                if not service_warranty_id:
                    raise ValidationError(_("Please select any one Service Warranty"))

                product_line_vals = vals.get('product_line_ids')
                lines_to_check = rec.product_line_ids if not product_line_vals else rec.product_line_ids
                ''' Client asked to need not give any product in the product lines because they need to close the job card without product on Oct -06s -2025'''
                # if not lines_to_check:
                #     raise ValidationError(_("Please give any one of the Product in the product consume Part/services"))
                #

                for line in lines_to_check:
                    if line.product_id:
                        if not line.parts_reserved_bool:
                            raise ValidationError(_("Please check all the Products should be Reserved. "
                                                    "This Product %s is not reserved" % line.product_id.display_name))

                    '''Code is added on Oct -06-2025 due to Client ask to skip the validation when negative_stock_allow allow field is enable in the res.config_settings'''
                    if not self.env['ir.config_parameter'].sudo().get_param(
                            'machine_repair_management.negative_stock_allow') == 'True':
                        if line.on_hand_qty == 0.0:
                            raise ValidationError(
                                _("Stock %s is not available. Please Contact Administrator" % line.product_id.display_name))

                if rec.inspection_charges_bool and rec.inspection_charges_amount > 0:
                    if not any(line.product_id and line.product_id.service_type_bool for line in rec.product_line_ids):
                        raise ValidationError(_("Please enter service charge amount in the product line"))

                if rec.service_sale_id:
                    if rec.service_sale_id.state not in ('sale', 'done'):
                        raise ValidationError("Please Confirm the Sale Quotation %s" % rec.service_sale_id.name)

                if rec.balance_paid != 0.0:
                    raise ValidationError("Balance Payment is there.Please Do the balance payment. ")

                if rec.hyperpay_line_ids:
                    for line in rec.hyperpay_line_ids:
                        if line.hyper_pay_status != 'success':
                            raise ValidationError("Still Payment is not Success.Please Check that")

            if state_changing_to_113:
                service_warranty = vals.get('service_warranty_id') or rec.service_warranty_id

                if not service_warranty:
                    raise ValidationError(_("Please select any one Service Warranty"))

                if rec.warranty:
                    purchase_invoice_no = vals.get('purchase_invoice_no') or rec.purchase_invoice_no
                    if not purchase_invoice_no:
                        raise ValidationError(_("Please enter Purchase Invoice No in the Job Card"))

                    purchase_date = vals.get('purchase_date') or rec.purchase_date
                    if not purchase_date:
                        raise ValidationError(_("Please enter Purchase date in the Job Card"))

                    dealer = vals.get('dealer_id') or rec.dealer_id
                    if not dealer:
                        raise ValidationError(_("Please enter Dealer Name in the Job Card"))

                    attachment_vals = vals.get('attachment_ids') or rec.attachment_ids
                    if not attachment_vals:
                        raise ValidationError(_('Please Attach Invoice Documents'))
                    if attachment_vals:
                        allowed_mimetypes = ['image/jpeg', 'image/png', 'image/gif', 'application/pdf']
                        for attachment in rec.attachment_ids:
                            if attachment.mimetype not in allowed_mimetypes:
                                raise ValidationError(_(
                                    "Only PDF, JPG, PNG, and GIF files are allowed in the job card.\n"
                                    f"Invalid file: {attachment.name}"
                                ))

            if state_changing_to_115 or state_changing_to_117 or state_changing_to_121 or state_changing_to_129:

                product_id = vals.get('product_id') or rec.product_id.id
                project_related_amc_bool = vals.get('project_related_amc_bool') or rec.project_related_amc_bool

                if not product_id and not project_related_amc_bool:
                    raise ValidationError(_("Please enter Model No. in the Job card"))

                product_slno = vals.get('product_slno') or rec.product_slno

                if not product_slno:
                    raise ValidationError(_("Please enter Serial Number in the Job Card"))

                service_warranty = vals.get('service_warranty_id') or rec.service_warranty_id

                if not service_warranty:
                    raise ValidationError(_("Please select any one Service Warranty"))

            if state_changing_to_121 or state_changing_to_128 or state_changing_to_125 or state_changing_to_117 or state_changing_to_116 or state_changing_to_129 or state_changing_to_130:
                # if state_changing_to_121 or state_changing_to_128 or state_changing_to_125 or state_changing_to_117 or state_changing_to_116 or state_changing_to_107 or state_changing_to_129 or state_changing_to_130:

                symptom_line_ids = vals.get('symptoms_line_ids_duplicate') or vals.get('symptoms_line_ids')
                lines_to_check = rec.symptoms_line_ids or symptom_line_ids
                if not lines_to_check:
                    raise ValidationError(_("Please give any one of the Symptoms in the Symptoms tab"))

                defect_type_ids = vals.get('defects_type_ids_duplicate') or vals.get('defects_type_ids')
                defect_to_check = rec.defects_type_ids or defect_type_ids
                if not defect_to_check:
                    raise ValidationError(_("Please give any one of the Defects in the Defects tab"))

                # service_type_ids = vals.get('service_type_ids_duplicate') or vals.get('service_type_ids')
                # service_to_check = rec.service_type_ids or service_type_ids
                # if not service_to_check:
                #     raise ValidationError(_("Please give any one of the Service in the Service tab"))

            if state_changing_to_112:
                symptom_line_ids = vals.get('symptoms_line_ids_duplicate') or vals.get('symptoms_line_ids')
                lines_to_check = rec.symptoms_line_ids or symptom_line_ids
                if not lines_to_check:
                    raise ValidationError(_("Please give any one of the Symptoms in the Symptoms tab"))

            if state_changing_to_117:

                engineer_comments = vals.get('engineer_comments') or rec.engineer_comments
                if not engineer_comments:
                    raise ValidationError(_("Please enter the Technician Comments 1"))

            if state_changing_to_125:

                service_type_ids = vals.get('service_type_ids_duplicate') or vals.get('service_type_ids')
                service_to_check = rec.service_type_ids or service_type_ids
                if not service_to_check:
                    raise ValidationError(_("Please give any one of the Service in the Service tab"))

                if self.second_visit_technician_bool:
                    engineer_comments_2 = vals.get('engineer_comments_second') or rec.engineer_comments_second
                    if not engineer_comments_2:
                        raise ValidationError(_("Please enter the Technician Comments 2"))

            '''If already warranty verification is there the job card is not open.It will be added on Sep 15 -2025 '''
            # it is already worked perfectly when we open the  already job card it will raise error.so it was commented
            # state_changing_to_111 = ('job_state' in vals or 'job_card_state_code' in vals) and (
            # (vals.get('job_card_state_code') == '111') or
            # (not vals.get('job_card_state_code') and vals.get('job_state') and
            #  self.env['project.task.type'].browse(vals['job_state']).code == '111')
            # )
            #
            # if state_code == '102':
            #     if not team_id:
            #         raise ValidationError(
            #             _("Please select a Team before moving Job Card %s") % rec.name
            #         )
            #
            # if state_code == '124' and not engineer_comments:
            #     raise ValidationError(
            #         _("Please enter Engineer Comments before moving Job Card %s") % rec.name
            #     )
            #
            # if state_code == '122':
            #     if not rec.product_line_ids and not vals.get("product_line_ids"):
            #         raise ValidationError(
            #             _("Please give at least one Product in the product consume Part/services")
            #         )
            #
            #     for line in rec.product_line_ids:
            #         if line.product_id:
            #             if not line.parts_reserved_bool:
            #                 raise ValidationError(
            #                     _("Product %s is not reserved. Please reserve all products before proceeding.")
            #                     % line.product_id.display_name
            #                 )
            #         if line.on_hand_qty == 0.0:
            #             raise ValidationError(
            #                 _("Stock is not available for Product %s. Please contact Administrator.")
            #                 % line.product_id.display_name
            #             )
            #
            #     # Inspection charges check
            #     if rec.inspection_charges_bool and rec.inspection_charges_amount > 0:
            #         if not any(
            #             l.product_id and l.product_id.service_type_bool
            #             for l in rec.product_line_ids
            #         ):
            #             raise ValidationError(
            #                 _("Please enter service charge amount in the product line")
            #             )
            #
            # if state_code == '125':
            #     product_id = vals.get('product_id') or rec.product_id.id
            #     if not product_id:
            #         raise ValidationError(_("Please enter Model No. in the Job card"))
            #     product_slno = vals.get('product_slno') or rec.product_slno
            #     # product_slno = vals['product_slno'] if 'product_slno' in vals else rec.product_slno
            #     # product_slno = vals.get('product_slno', rec.product_slno)
            #     if not product_slno:
            #         raise ValidationError(_("Please enter Serial Number in the Job card"))
            #
            #     purchase_invoice_no = vals.get('purchase_invoice_no') or rec.purchase_invoice_no
            #     if rec.warranty and not purchase_invoice_no:
            #         raise ValidationError(_("Please enter Purchase Invoice No in the Job Card"))
            #
            #     purchase_date = vals.get('purchase_date') or rec.purchase_date
            #     if rec.warranty and not purchase_date:
            #         raise ValidationError(_("Please enter Purchase date in the Job card"))
            #
            #     service_warranty_id = vals.get('service_warranty_id') or rec.service_warranty_id.id
            #     if not service_warranty_id:
            #         raise ValidationError(_("Please select any one Service Warranty in the Job Card"))
            #
            # if state_code == '126':
            #     control_card_no = vals.get('control_card_no') or rec.control_card_no
            #     if not control_card_no:
            #         raise ValidationError(_("Please enter 'Control Card No' in the Job Card."))
            #
            #     closed_datetime = vals.get('closed_datetime') or rec.closed_datetime
            #     if not closed_datetime:
            #         raise ValidationError(_("Please enter Completed Date & Time in the Job Card"))
            #
            #     if closed_datetime:
            #         if rec.planned_date_begin and closed_datetime:
            #             if rec.planned_date_begin > closed_datetime:
            #                 raise ValidationError('Completed Date & Time is always greater than Appt Start Date & Time')
            #
            #
            #     # job_card_completed_datetime = vals.get('job_card_completed_time') or rec.job_card_completed_time
            #     #
            #     # if not job_card_completed_datetime:
            #     #     raise ValidationError(_("Please enter Job Card Closed Date & Time in the Job Card"))
            #     #
            #
            #
            #     product_id = vals.get('product_id') or rec.product_id.id
            #     if not product_id:
            #         raise ValidationError(_("Please enter Model No. in the Job card"))
            #
            #     purchase_invoice_no = vals.get('purchase_invoice_no') or rec.purchase_invoice_no
            #     if rec.warranty and not purchase_invoice_no:
            #         raise ValidationError(_("Please enter Purchase Invoice No"))
            #
            #     purchase_date = vals.get('purchase_date') or rec.purchase_date
            #     if rec.warranty and not purchase_date:
            #         raise ValidationError(_("Please enter Purchase date in the Job card"))
            #
            #     # job_card_completed_time = vals.get('job_card_completed_time') or rec.job_card_completed_time
            #     # if not job_card_completed_time:
            #     #     raise ValidationError(_("Please enter Job Card Completed Time in the Job card"))
            #     #
            #
            #     service_warranty_id = vals.get('service_warranty_id') or rec.service_warranty_id.id
            #     if not service_warranty_id:
            #         raise ValidationError(_("Please select any one Service Warranty"))
            #
            #     product_line_vals = vals.get('product_line_ids')
            #     lines_to_check = rec.product_line_ids if not product_line_vals else rec.product_line_ids  # safer
            #     if not lines_to_check:
            #
            #     for line in lines_to_check:
            #         if line.product_id:
            #             if not line.parts_reserved_bool:
            #                 raise ValidationError(_("Please check all the Products should be Reserved. "
            #                                         "This Product %s is not reserved") % line.product_id.display_name)
            #         if line.on_hand_qty == 0.0:
            #             raise ValidationError(_("Stock is not available. Please Contact Administrator"))
            #
            #     if rec.inspection_charges_bool and rec.inspection_charges_amount > 0:
            #         if not any(line.product_id and line.product_id.service_type_bool for line in rec.product_line_ids):
            #             raise ValidationError(_("Please enter service charge amount in the product line"))
            #
            #
            # if state_changing_to_111:
            #     product_id = vals.get('product_id') or rec.product_id.id
            #     if not product_id:
            #         raise ValidationError(_("Please enter Model No. in the Job card"))
            #     product_slno = vals.get('product_slno') or rec.product_slno
            #
            #     if not product_slno:
            #         raise ValidationError(_("Please enter Serial Number in the Job Card"))

            warranty_fields_updated = any(field in vals for field in [
                'service_warranty_id', 'warranty', 'product_id', 'product_slno',
                'purchase_invoice_no', 'purchase_date', 'dealer_id', 'attachment_ids'
            ])

            if (warranty_fields_updated and
                    not self.env.context.get('skip_warranty_validation') and
                    not self.env.context.get('creating')):

                if rec.service_warranty_id or vals.get('service_warranty_id'):

                    warranty_status = vals.get('warranty') if 'warranty' in vals else rec.warranty
                    if warranty_status:
                        if not state_changing_to_113:
                            # if not self.env.context.get('skip_warranty_validation'):
                            #     if rec.service_warranty_id or vals.get('service_warranty_id'):
                            #         if rec.warranty:
                            ''' commented on Oct 17 due to warranty verification status in mobile they don't want to Model no and Serial number mandatory
                            product_id = vals.get('product_id') or rec.product_id.id
                            if not product_id:
                                raise ValidationError(_("Please enter Model No. in the Job card."))
                            product_slno = vals.get('product_slno') or rec.product_slno

                            if not product_slno:
                                raise ValidationError(_("Please enter Serial Number in the Job Card"))
                            '''
                            purchase_invoice_no = vals.get('purchase_invoice_no') or rec.purchase_invoice_no
                            if not purchase_invoice_no:
                                raise ValidationError(_("Please enter Purchase Invoice No in the Job Card"))

                            purchase_date = vals.get('purchase_date') or rec.purchase_date
                            if not purchase_date:
                                raise ValidationError(_("Please enter Purchase date in the Job Card"))

                            dealer = vals.get('dealer_id') or rec.dealer_id
                            if not dealer:
                                raise ValidationError(_("Please enter Dealer Name in the Job Card"))

                            attachment_vals = vals.get('attachment_ids') or rec.attachment_ids
                            if not attachment_vals:
                                raise ValidationError(_('Please Attach Invoice Documents'))
                            if attachment_vals:
                                allowed_mimetypes = ['image/jpeg', 'image/png', 'image/gif', 'application/pdf']
                                for attachment in rec.attachment_ids:
                                    if attachment.mimetype not in allowed_mimetypes:
                                        raise ValidationError(_(
                                            "Only PDF, JPG, PNG, and GIF files are allowed in the job card.\n"
                                            f"Invalid file: {attachment.name}"
                                        ))

                    # if rec.service_warranty_id.misuse_warranty_bool:
                    #     if not state_changing_to_113:
                    #         product_id = vals.get('product_id') or rec.product_id.id
                    #         if not product_id:
                    #             raise ValidationError(_("Please enter Model No. in the Job card"))
                    #         product_slno = vals.get('product_slno') or rec.product_slno
                    #
                    #         if not product_slno:
                    #             raise ValidationError(_("Please enter Serial Number in the Job Card"))
                    #
                    #         purchase_invoice_no = vals.get('purchase_invoice_no') or rec.purchase_invoice_no
                    #         if not purchase_invoice_no:
                    #             raise ValidationError(_("Please enter Purchase Invoice No in the Job Card"))
                    #
                    #         purchase_date = vals.get('purchase_date') or rec.purchase_date
                    #         if not purchase_date:
                    #             raise ValidationError(_("Please enter Purchase date in the Job Card"))
                    #
                    #         dealer = vals.get('dealer_id') or rec.dealer_id
                    #         if not dealer :
                    #             raise ValidationError(_("Please enter Dealer Name in the Job Card"))
                    #
                    #
                    #         attachment_vals = vals.get('attachment_ids') or rec.attachment_ids
                    #         if not attachment_vals:
                    #             raise ValidationError(_('Please Attach Invoice Documents'))
                    #         if attachment_vals:
                    #             allowed_mimetypes = ['image/jpeg', 'image/png', 'image/gif', 'application/pdf']
                    #             for attachment in rec.attachment_ids:
                    #                 if attachment.mimetype not in allowed_mimetypes:
                    #                     raise ValidationError(_(
                    #                         "Only PDF, JPG, PNG, and GIF files are allowed in the job card.\n"
                    #                         f"Invalid file: {attachment.name}"
                    #                     ))
                    #

                    # if not warranty_status:
                    #     product_id = vals.get('product_id') or rec.product_id.id
                    #     if not product_id:
                    #         raise ValidationError(_("Please enter Model No. in the Job card number"))
                    #     product_slno = vals.get('product_slno') or rec.product_slno
                    #
                    #     if not product_slno:
                    #         raise ValidationError(_("Please enter Serial Number in the Job Card number"))
                    #

            # if rec.closed_datetime or vals.get('closed_datetime'):
            #     if rec.planned_date_begin and rec.closed_datetime:
            #         if rec.planned_date_begin > rec.closed_datetime:
            #             raise ValidationError('Closed Date & Time is always greater than Appt Start Date & Time')
            #

        res = super().write(vals)

        state_date_map = {
            '103': 'technician_accepted_date',
            '104': 'technician_rejected_date',
            '109': 'technician_started_date',
            '110': 'technician_reached_date',
            '115': 'job_started_date',
            '121': 'job_hold_date',
            '122': 'job_resume_date',
            '123': 'job_resume_date',
            '124': 'cancel_date_time',
            # '125':'closed_datetime',
            '126': 'job_card_completed_time',
            ## this code is added on Oct  23 2025 they want technician first time and second time date time field
            # '110':'technician_first_visit_datetime',

        }
        if vals.get('job_state'):
            state = self.env['project.task.type'].sudo().browse(vals['job_state'])
            if not state.exists():
                vals['job_state'] = False

            if state:

                if 'job_state' in vals:
                    old_code = self.job_card_state_code
                    if old_code:
                        self.previous_job_card_state_code = old_code

                valid_codes = self.env['project.task.type'].sudo().search([]).mapped('code')

                # if state.code in ('103', '104', '105', '106', '107', '108', '109', '110', '111', '112', '113', '114', '115', '116', '117', '118', '119',
                #                   '120', '121', '122', '123', '124', '125', '126', '127', '128','129','130','131', '132', '133', '134','201','202','203','204','205','152','154','156'):

                if state.code in valid_codes:
                    self.job_card_state = state.name
                    self.job_card_state_code = state.code
                    self.service_request_id.service_request_state = state.name
                    self.service_request_id.service_request_state_code = state.code
                    self.service_request_id.state = vals.get('job_state')

                if state.code in state_date_map:
                    '''
                    if state.code is 103:
                    state_date_mapping[state.code] returns 'technician_accepted_date'.
                    self['technician_accepted_date'] accesses the technician_accepted_date field on the record.
                    '''
                    self[state_date_map[state.code]] = fields.Datetime.now()

                if state.code == '117':
                    '''If Unit pull out don't want to second vist to be bool added on Nov -01-2025'''
                    # self.second_visit_technician_bool = True
                    self._send_unit_receipt_whatsapp()
                    today = fields.Datetime.now()
                    user_tz = self.env.user.tz or 'UTC'
                    user_timezone = pytz.timezone(user_tz)
                    local_dt = pytz.utc.localize(today).astimezone(user_timezone)
                    self.technician_first_outtime = local_dt.strftime("%H:%M:%S")

                if state.code == '105':
                    self._send_failed_to_attend_call_status_whatsapp()

                if state.code == '125':
                    if not self.job_card_closed_date_time_enable:
                        self.closed_datetime = fields.Datetime.now()
                    if self.second_visit_technician_bool:
                        today = fields.Datetime.now()
                        user_tz = self.env.user.tz or 'UTC'
                        user_timezone = pytz.timezone(user_tz)
                        local_dt = pytz.utc.localize(today).astimezone(user_timezone)
                        self.technician_second_outtime = local_dt.strftime("%H:%M:%S")
                    if not self.second_visit_technician_bool:
                        today = fields.Datetime.now()
                        user_tz = self.env.user.tz or 'UTC'
                        user_timezone = pytz.timezone(user_tz)
                        local_dt = pytz.utc.localize(today).astimezone(user_timezone)
                        self.technician_first_outtime = local_dt.strftime("%H:%M:%S")

                    if self.inspection_charges_amount > 0 or self.service_warranty_id:
                        not_under_warranty = False
                        for line in self.product_line_ids:
                            if not line.under_warranty_bool:
                                if line.total > 0:
                                    not_under_warranty = True
                        if not_under_warranty:
                            self.send_whatsapp_service_charges_receipt()
                    self._send_whatsapp_job_card_report_for_ready_to_invoice()

                if state.code == '110':
                    if not self.second_visit_technician_bool:
                        self.technician_first_visit_datetime = fields.Datetime.now()
                        self.technician_first_visit_date = fields.Date.today()
                    if self.second_visit_technician_bool:
                        self.technician_second_visit_datetime = fields.Datetime.now()
                        self.technician_second_visit_date = fields.Date.today()

                if state.code == '112':
                    self.cancellation_reason_id = self.env['cancellation.reason'].search(
                        [('name', 'ilike', 'Cancelled. Insp Chrg Rej by Cst')], limit=1).id
                    self._send_whatsapp_for_cancelled_insp_charges_by_cst()
                    if self.inspection_charges_amount > 0:
                        self.send_whatsapp_service_charges_receipt()

                if state.code == '113':
                    self.create_quotation_show_bool = True
                    if self.inspection_charges_amount > 0:
                        self.send_whatsapp_service_charges_receipt()

                if state.code == '121':
                    today = fields.Datetime.now()
                    user_tz = self.env.user.tz or 'UTC'
                    user_timezone = pytz.timezone(user_tz)
                    local_dt = pytz.utc.localize(today).astimezone(user_timezone)

                    self.technician_first_outtime = local_dt.strftime("%H:%M:%S")
                    # user_tz= self.env.user.tz
                    self.second_visit_technician_bool = True
                    self._send_email_for_parts_user()
                    self._send_whatsapp_for_parts_user()
                    self._send_whatsapp_job_card_report_for_ready_to_invoice()

                if state.code == '122':
                    self._send_email_for_supervisor_user()
                    self._send_whatsapp_for_supervisor_user()

                    # if state.code == '124':
                #     self._send_whatsapp_for_cancellation()

                if state.code == '126':
                    self.job_card_completed_time = fields.Datetime.now()
                    if self.inspection_charges_amount > 0 or self.service_warranty_id:
                        not_under_warranty = False
                        for line in self.product_line_ids:
                            if not line.under_warranty_bool:
                                if line.total > 0:
                                    not_under_warranty = True
                        if not_under_warranty:
                            self.send_whatsapp_invoice_receipt()

                    # self.send_whatsapp_invoice_receipt()

                if state.code == '128':
                    if self.inspection_charges_amount > 0:
                        self.send_whatsapp_service_charges_receipt()

                if state.code == '129':
                    today = fields.Datetime.now()
                    user_tz = self.env.user.tz or 'UTC'
                    user_timezone = pytz.timezone(user_tz)
                    local_dt = pytz.utc.localize(today).astimezone(user_timezone)

                    self.technician_first_outtime = local_dt.strftime("%H:%M:%S")
                    self.second_visit_technician_bool = True

                if state.code == '130':
                    today = fields.Datetime.now()
                    user_tz = self.env.user.tz or 'UTC'
                    user_timezone = pytz.timezone(user_tz)
                    local_dt = pytz.utc.localize(today).astimezone(user_timezone)

                    self.technician_first_outtime = local_dt.strftime("%H:%M:%S")
                    self.second_visit_technician_bool = True

                if state.code == '132':
                    self.second_visit_technician_bool = True

                if state.code == '134':
                    self._send_whatsapp_for_rescheduled_with_parts()

                if state.code == '116':
                    today = fields.Datetime.now()
                    user_tz = self.env.user.tz or 'UTC'
                    user_timezone = pytz.timezone(user_tz)
                    local_dt = pytz.utc.localize(today).astimezone(user_timezone)

                    self.technician_first_outtime = local_dt.strftime("%H:%M:%S")
                    self.second_visit_technician_bool = True

                if state.code == '107':
                    today = fields.Datetime.now()
                    user_tz = self.env.user.tz or 'UTC'
                    user_timezone = pytz.timezone(user_tz)
                    local_dt = pytz.utc.localize(today).astimezone(user_timezone)

                    self.technician_first_outtime = local_dt.strftime("%H:%M:%S")
                    # self.second_visit_technician_bool = True

                    self.team_id = False
                    self.technician_id = False

                    self.planned_date_begin = False
                    self.planned_date_end = False

                    ''' Code is added on Vijaya Bhaskar on Nov 10 2025 '''
                    self.technician_first_visit_id = False
                    self.technician_first_visit = False
                    self.technician_first_visit_date = False
                    self.technician_first_intime = False
                    self.technician_first_outtime = False

                ''' Code is added on Vijaya Bhaskar on Nov 11 2025 '''

                if state.code == '156':
                    self.team_id = False
                    self.technician_id = False
                    self.planned_date_begin = False
                    self.planned_date_end = False
                    self.cancellation_reason_id = False

                    self.technician_first_visit_id = False
                    self.technician_first_visit = False
                    self.technician_first_visit_date = False
                    self.technician_first_intime = False
                    self.technician_first_outtime = False

                # if state.code == '133':
                #     self.team_id = False
                #     self.planned_date_begin = False
                #     self.planned_date_end = False
                #

                # if state.code  == '102':
                #     team_id_val = vals.get('team_id') or self.team_id.id
                #     self.technician_accepted_status_check = True
                #
                #     if not team_id_val:
                #         raise ValidationError(
                #             _("Please enter a Team Leader before setting Job Card %s.") % self.name
                #         )
                #

                # if state.code  == '101':
                #     self.technician_accepted_status_check = True

                # oct 31 2025
                if state.code == '102':
                    team_id_val = vals.get('team_id') or self.team_id.id
                    self.technician_accepted_status_check = True

                    if not team_id_val:
                        raise ValidationError(
                            _("Please enter a Team Leader before setting Job Card %s.") % self.name
                        )

                    technician_users = self.technician_id
                    odoo_bot = self.env.user.partner_id
                    if technician_users.partner_id:
                        # Create or fetch private chat channel
                        channel_name = f"{odoo_bot.name}, {technician_users.name}"
                        channel = self.env['discuss.channel'].search([
                            ('name', 'ilike', channel_name),
                            ('channel_type', '=', 'chat')
                        ], limit=1)
                        if not channel:
                            channel = self.env['discuss.channel'].create({
                                'name': channel_name,
                                'channel_type': 'chat',
                                'channel_partner_ids': [(4, technician_users.partner_id.id)]
                            })
                        planned_plus_3 = False
                        if self.planned_date_begin:
                            planned_plus_3 = self.planned_date_begin + timedelta(hours=3)

                            message_body = (
                                f'Job Card {self.name} has been assigned to Mr. {self.technician_id.name} '
                                f'at {planned_plus_3.strftime("%d-%m-%Y %H:%M:%S")}.'
                            )
                            channel.message_post(
                                body=message_body,
                                subject='Job Card State Update',
                                message_type='notification',
                                subtype_xmlid='mail.mt_comment',
                                author_id=odoo_bot.id
                            )

                elif state.code == '103':
                    self.technician_accepted_status_check = False

                elif state.code == '104':
                    work_center = self.technician_id.default_work_center_id
                    if not work_center:
                        _logger.warning(
                            "No work center found for technician %s on Job Card %s",
                            self.technician_id.name, self.name
                        )
                        return

                    finance_users = self.env['res.users'].search([
                        ('default_work_center_id', '=', work_center.id),
                        (
                        'groups_id', 'in', self.env.ref('machine_repair_management.group_technical_allocation_user').id)
                    ])

                    odoo_bot = self.env.ref('base.partner_root')
                    for user in finance_users:
                        if user.partner_id:
                            channel_name = f"{odoo_bot.name}, {user.name}"
                            channel = self.env['discuss.channel'].search([
                                ('name', 'ilike', channel_name),
                                ('channel_type', '=', 'chat')
                            ], limit=1)
                            if not channel:
                                channel = self.env['discuss.channel'].create({
                                    'name': channel_name,
                                    'channel_type': 'chat',
                                    'channel_partner_ids': [(4, user.partner_id.id)]
                                })
                            channel.message_post(
                                body=f'Technician {self.technician_id.name} has rejected Job Card {self.name} (Work Center: {work_center.name})',
                                subject='Job Card State Update',
                                message_type='notification',
                                subtype_xmlid='mail.mt_comment',
                                author_id=odoo_bot.id
                            )

                elif state.code == '107':
                    self._send_notification_to_supervisior()

                elif state.code == '121':
                    work_center = self.technician_id.default_work_center_id
                    group_id = self.env.ref('machine_repair_management.group_parts_user').id
                    finance_users = self.env['res.users'].search([
                        ('groups_id', 'in', [group_id]),
                        ('default_work_center_id', '=', work_center.id)
                    ])

                    odoo_bot = self.env.user.partner_id
                    for user in finance_users:
                        if user.partner_id:
                            channel_name = f"{odoo_bot.name}, {user.name}"
                            channel = self.env['discuss.channel'].search([
                                ('name', 'ilike', channel_name),
                                ('channel_type', '=', 'chat')
                            ], limit=1)
                            if not channel:
                                channel = self.env['discuss.channel'].create({
                                    'name': channel_name,
                                    'channel_type': 'chat',
                                    'channel_partner_ids': [(4, user.partner_id.id)]
                                })
                            message_body = f'Technician {self.technician_id.name} has put Job Card {self.name} on hold due to stock not available for some of the items.'
                            channel.message_post(
                                body=message_body,
                                subject='Job Card State Update',
                                message_type='notification',
                                subtype_xmlid='mail.mt_comment',
                                author_id=odoo_bot.id
                            )
                elif state.code == '122':
                    self._send_email_for_supervisor_user()
                    self._send_whatsapp_for_supervisor_user()

                    work_center = self.technician_id.default_work_center_id
                    group_id = self.env.ref('machine_repair_management.group_technical_allocation_user').id

                    finance_users = self.env['res.users'].search([
                        ('groups_id', 'in', [group_id]),
                        ('default_work_center_id', '=', work_center.id)
                    ])

                    odoo_bot = self.env.user.partner_id

                    for user in finance_users:
                        if not user.partner_id:
                            continue

                        channel_name = f"{odoo_bot.name}, {user.name}"
                        channel = self.env['discuss.channel'].search([
                            ('name', 'ilike', channel_name),
                            ('channel_type', '=', 'chat')
                        ], limit=1)
                        if not channel:
                            channel = self.env['discuss.channel'].create({
                                'name': channel_name,
                                'channel_type': 'chat',
                                'channel_partner_ids': [(4, user.partner_id.id), (4, odoo_bot.id)],
                            })

                        message_body = f'Co-ordinator {user.name} has put Job Card {self.name} parts are ready.'

                        # Send message to the user
                        channel.message_post(
                            body=message_body,
                            subject='Job Card State Update',
                            message_type='notification',
                            subtype_xmlid='mail.mt_comment',
                            author_id=odoo_bot.id,
                        )

                elif state.code == '124':
                    self._send_notification_to_technician()

                elif state.code == '125':
                    work_center = self.technician_id.default_work_center_id
                    finance_users = self.env['res.users'].search([
                        ('default_work_center_id', '=', work_center.id),
                        (
                        'groups_id', 'in', self.env.ref('machine_repair_management.group_technical_allocation_user').id)
                    ])

                    odoo_bot = self.env.user.partner_id
                    for user in finance_users:
                        if user.partner_id:
                            channel_name = f"{odoo_bot.name}, {user.name}"
                            channel = self.env['discuss.channel'].search([
                                ('name', 'ilike', channel_name),
                                ('channel_type', '=', 'chat')
                            ], limit=1)
                            if not channel:
                                channel = self.env['discuss.channel'].create({
                                    'name': channel_name,
                                    'channel_type': 'chat',
                                    'channel_partner_ids': [(4, user.partner_id.id)]
                                })
                            message_body = f'Job Card {self.name} has been completed and is ready to be invoiced.'
                            channel.message_post(
                                body=message_body,
                                subject='Job Card State Update',
                                message_type='notification',
                                subtype_xmlid='mail.mt_comment',
                                author_id=odoo_bot.id
                            )

                # if  state.code == '103':
                #     self.technician_accepted_status_check = False
                #
                # elif state.code == '104':
                #     work_center = self.technician_id.default_work_center_id
                #     if not work_center:
                #         _logger.warning("No work center found for technician %s on Job Card %s", self.technician_id.name,
                #                         rec.name)
                #         return
                #     # Search for finance users with the specified group and work center
                #     finance_users = self.env['res.users'].search([
                #         ('default_work_center_id', '=', work_center.id),
                #         (
                #         'groups_id', 'in', self.env.ref('machine_repair_management.group_technical_allocation_user').id)
                #     ])
                #     # OdooBot as the sender
                #     odoo_bot = self.env.ref('base.partner_root')
                #     # Post message to each user's private Discuss channel
                #     for user in finance_users:
                #         if user.partner_id:
                #             # Find or create a private channel between OdooBot and the user
                #             channel_name = f"{odoo_bot.name}, {user.name}"
                #             channel = self.env['discuss.channel'].search([
                #                 ('name', 'ilike', channel_name),
                #                 ('channel_type', '=', 'chat')
                #             ], limit=1)
                #             if not channel:
                #                 channel = self.env['discuss.channel'].create({
                #                     'name': channel_name,
                #                     'channel_type': 'chat',
                #                     # 'public': 'private',
                #                     'channel_partner_ids': [(4, user.partner_id.id)]
                #                 })
                #             # Post the message to the private channel
                #
                #             channel.message_post(
                #                 body=f'Technician {self.technician_id.name} has rejected Job Card {self.name} (Work Center: {work_center.name})',
                #                 subject='Job Card State Update',
                #                 message_type='notification',
                #                 subtype_xmlid='mail.mt_comment',
                #                 author_id=odoo_bot.id
                #             )
                #
                # elif state.code == '121':
                #     work_center = self.technician_id.default_work_center_id
                #
                #     # Fetch finance users from the group
                #     group_id = self.env.ref('machine_repair_management.group_parts_user').id
                #     finance_users = self.env['res.users'].search([('groups_id', 'in', [group_id]), ('default_work_center_id', '=', work_center.id)])
                #
                #     # OdooBot as sender
                #     odoo_bot = self.env.ref('base.partner_root')
                #
                #     for user in finance_users:
                #         if user.partner_id:
                #
                #             # Create or fetch private chat channel
                #             channel_name = f"{odoo_bot.name}, {user.name}"
                #             channel = self.env['discuss.channel'].search([
                #                 ('name', 'ilike', channel_name),
                #                 ('channel_type', '=', 'chat')
                #             ], limit=1)
                #             if not channel:
                #                 channel = self.env['discuss.channel'].create({
                #                     'name': channel_name,
                #                     'channel_type': 'chat',
                #                     # 'public': 'private',
                #                     'channel_partner_ids': [(4, user.partner_id.id)]
                #                 })
                #
                #             # Post message
                #             message_body = f'Technician {self.technician_id.name} has put Job Card {self.name} on hold.'
                #             channel.message_post(
                #                 body=message_body,
                #                 subject='Job Card State Update',
                #                 message_type='notification',
                #                 subtype_xmlid='mail.mt_comment',
                #                 author_id=odoo_bot.id
                #             )
                # elif state.code == '122':
                #     work_center = self.technician_id.default_work_center_id
                #
                #     # Fetch finance users from the group
                #     group_id = self.env.ref('machine_repair_management.group_parts_user').id
                #     finance_users = self.env['res.users'].search([('groups_id', 'in', [group_id]), ('default_work_center_id', '=', work_center.id)])
                #
                #     # OdooBot as sender
                #     odoo_bot = self.env.ref('base.partner_root')
                #
                #     for user in finance_users:
                #         if user.partner_id:
                #
                #             # Create or fetch private chat channel
                #             channel_name = f"{odoo_bot.name}, {user.name}"
                #             channel = self.env['discuss.channel'].search([
                #                 ('name', 'ilike', channel_name),
                #                 ('channel_type', '=', 'chat')
                #             ], limit=1)
                #             if not channel:
                #                 channel = self.env['discuss.channel'].create({
                #                     'name': channel_name,
                #                     'channel_type': 'chat',
                #                     # 'public': 'private',
                #                     'channel_partner_ids': [(4, user.partner_id.id)]
                #                 })
                #
                #             # Post message
                #             message_body = f'Technician {self.technician_id.name} has put Job Card {self.name} on hold.'
                #             channel.message_post(
                #                 body=message_body,
                #                 subject='Job Card State Update',
                #                 message_type='notification',
                #                 subtype_xmlid='mail.mt_comment',
                #                 author_id=odoo_bot.id
                #             )
                #
                # elif state.code == '125':
                #     work_center = self.technician_id.default_work_center_id
                #
                #     finance_users = self.env['res.users'].search([
                #         ('default_work_center_id', '=', work_center.id),
                #         (
                #             'groups_id', 'in',
                #             self.env.ref('machine_repair_management.group_technical_allocation_user').id)
                #     ])
                #
                #     # if finance_users and rec.technician_id.partner_id:
                #     #     technician_user = rec.technician_id
                #     #     technician_partner = technician_user.partner_id
                #     odoo_bot = self.env.ref('base.partner_root')
                #
                #     # Combine partner IDs into a single flat list
                #     for user in finance_users:
                #         if user.partner_id:
                #             # Find or create a private channel between OdooBot and the user
                #             channel_name = f"{odoo_bot.name}, {user.name}"
                #             channel = self.env['discuss.channel'].search([
                #                 ('name', 'ilike', channel_name),
                #                 ('channel_type', '=', 'chat')
                #             ], limit=1)
                #             if not channel:
                #                 channel = self.env['discuss.channel'].create({
                #                     'name': channel_name,
                #                     'channel_type': 'chat',
                #                     # 'public': 'private',
                #                     'channel_partner_ids': [(4, user.partner_id.id)]
                #                 })
                #
                #             # Post the message
                #             message_body = f'Job Card {self.name} has been completed and is ready to be invoiced.'
                #             channel.message_post(
                #                 body=message_body,
                #                 subject='Job Card State Update',
                #                 message_type='notification',
                #                 subtype_xmlid='mail.mt_comment',
                #                 author_id=odoo_bot.id,
                #             )
                #
                # elif state.code == '102':
                #
                #     technician_users = self.technician_id
                #     # OdooBot as sender
                #     odoo_bot = self.env.ref('base.partner_root')
                #     # for user in technician_users:
                #     if technician_users.partner_id:
                #         # Create or fetch private chat channel
                #         channel_name = f"{odoo_bot.name}, {technician_users.name}"
                #         channel = self.env['discuss.channel'].search([
                #             ('name', 'ilike', channel_name),
                #             ('channel_type', '=', 'chat')
                #         ], limit=1)
                #         if not channel:
                #             channel = self.env['discuss.channel'].create({
                #                 'name': channel_name,
                #                 'channel_type': 'chat',
                #                 # 'public': 'private',
                #                 'channel_partner_ids': [(4, technician_users.partner_id.id)]
                #             })
                #
                #         # Post message
                #         message_body = (
                #             f'Job Card {self.name} has been assigned to Mr. {self.technician_id.name}.'
                #         )
                #         channel.message_post(
                #             body=message_body,
                #             subject='Job Card State Update',
                #             message_type='notification',
                #             subtype_xmlid='mail.mt_comment',
                #             author_id=odoo_bot.id
                #         )

                # if state.code == '124':
                #     return self.cancelled_reason_button_mobile()
        for record in self:

            if vals.get('team_id') and record.service_request_id:
                record.service_request_id.team_id = vals.get('team_id')
                record.service_request_id._onchange_team_id()

                # if not record.second_visit_technician_bool:
                #     record.technician_first_visit_id = record.team_id.id
                # else:
                #     record.technician_second_visit_id = vals.get('team_id')
                #
                ''' This code is correctly worked but they want after change first time unit pull out if technician changes 
                    then need not changed the state as scheduled they want Rescheduled for internal technician  
                scheduled_state = self.env['project.task.type'].search(
                        [('code', '=', '102')], limit=1
                    )
                if scheduled_state:
                    record.job_state = scheduled_state.id
                    record.job_card_state = record.job_state.name
                    record.job_card_state_code = record.job_state.code

                    record.service_request_id.service_request_state = record.job_state.name
                    record.service_request_id.service_request_state_code = record.job_state.code
                    record.service_request_id.state = record.job_state
                '''
                '''This code is added on Nov-01-2025 '''
                # print("..................................record.job_card_state_code",record.job_card_state_code)
                if not record.job_card_state_code in ('117', '132'):
                    scheduled_state = self.env['project.task.type'].search(
                        [('code', '=', '102')], limit=1
                    )
                    if scheduled_state:
                        record.job_state = scheduled_state.id
                        record.job_card_state = record.job_state.name
                        record.job_card_state_code = record.job_state.code

                        record.service_request_id.service_request_state = record.job_state.name
                        record.service_request_id.service_request_state_code = record.job_state.code
                        record.service_request_id.state = record.job_state

                if record.job_card_state_code == '117':
                    scheduled_state = self.env['project.task.type'].search(
                        [('code', '=', '204')], limit=1
                    )
                    if scheduled_state:
                        record.job_state = scheduled_state.id
                        record.job_card_state = record.job_state.name
                        record.job_card_state_code = record.job_state.code

                        record.service_request_id.service_request_state = record.job_state.name
                        record.service_request_id.service_request_state_code = record.job_state.code
                        record.service_request_id.state = record.job_state

                if record.job_card_state_code == '132':
                    # record.second_visit_technician_bool = True
                    scheduled_state = self.env['project.task.type'].search(
                        [('code', '=', '133')], limit=1
                    )
                    if scheduled_state:
                        record.job_state = scheduled_state.id
                        record.job_card_state = record.job_state.name
                        record.job_card_state_code = record.job_state.code

                        record.service_request_id.service_request_state = record.job_state.name
                        record.service_request_id.service_request_state_code = record.job_state.code
                        record.service_request_id.state = record.job_state

                        # record._onchange_job_card_state_status()
                # record._send_whatsapp_scheduled_message()
                # record._send_whatsapp_scheduled_technician_message()
                #

            if vals.get('planned_date_begin') and vals.get('team_id') and record.service_request_id:
                record.service_request_id.technician_appointment_date = vals.get('planned_date_begin')
                # record._send_whatsapp_scheduled_message()
                # record._send_whatsapp_scheduled_technician_message()

            if vals.get('service_requested_datetime') and record.service_request_id:
                record.service_request_id.call_request_appointment_date = vals.get('service_requested_datetime')

            if vals.get('attachment_ids') and record.service_request_id:
                record.service_request_id.attachment_ids = vals.get('attachment_ids')

            if vals.get('service_warranty_id') and record.service_warranty_id:
                record.service_request_id.sr_service_warranty_id = vals.get('service_warranty_id')

            if vals.get('purchase_invoice_no') and record.service_warranty_id:
                record.service_request_id.purchase_invoice_no = vals.get('purchase_invoice_no')

            if vals.get('purchase_date') and record.service_warranty_id:
                record.service_request_id.purchase_date = vals.get('purchase_date')

            if vals.get('dealer_id') and record.service_request_id:
                record.service_request_id.dealer_id = vals.get('dealer_id')

            if vals.get('warranty_expiry_date') and record.service_request_id:
                record.service_request_id.website_year = vals.get('warranty_expiry_date')

            if vals.get('product_id') and record.service_request_id:
                record.service_request_id.product_id = vals.get('product_id')

            if vals.get('product_sub_group_id') and record.service_request_id:
                record.service_request_id.product_sub_group_id = vals.get('product_sub_group_id')

            if vals.get('svc_id') and record.service_request_id:
                record.service_request_id.svc_id = vals.get('svc_id')

            if vals.get('product_slno') and record.service_request_id:
                record.service_request_id.product_slno = vals.get('product_slno')

            if vals.get('inspection_charges_bool') or vals.get('inspection_charges_amount'):

                ''' the client asked to even inspection charges amount is zero they want to create service item on the product lines.Added on Oct-10-2025  

                if rec.inspection_charges_amount > 0 and rec.inspection_charges_bool and rec.warehouse_id:
                '''
                if rec.inspection_charges_bool and rec.warehouse_id:

                    service_lines = rec.product_line_ids.filtered(
                        lambda line: line.product_id.service_type_bool
                    )
                    # Search for service product in warehouse
                    stock_quant = self.env['stock.quant'].search([
                        ('product_id.service_type_bool', '=', True),
                        ('location_id', '=', rec.warehouse_id.lot_stock_id.id)
                    ], limit=1)

                    if stock_quant:
                        product = stock_quant.product_id
                        price_unit = rec.inspection_charges_amount
                        vat_taxes = product.taxes_id
                        vat_amount = 0.0
                        if vat_taxes:
                            vat_amount = vat_taxes[0].amount
                            tax_factor = 1 + (vat_amount / 100)
                            price_unit /= tax_factor

                        # Set additional fields similar to _product_line_onchange without overwriting price_unit
                        uom_id = product.uom_id.id

                        '''For Mis use Warranty Service Product warranty is untick code is added on Nov 05-2025 '''
                        if rec.service_warranty_id.misuse_warranty_bool:
                            rec.warranty = False

                        under_warranty = rec.warranty
                        standard_price = product.lst_price
                        on_hand_qty = stock_quant.quantity if stock_quant else 0.0

                        quantity_search = self.env['stock.quant'].search([('product_id', '=', product.id)])
                        overall_qty = sum(quant.quantity for quant in quantity_search) if quantity_search else 0.0

                        parts_reserved_bool = rec.warranty

                        vals = {
                            'product_id': product.id,
                            # 'price_unit': price_unit,
                            'price_unit': price_unit if not rec.warranty else 0.0,
                            'qty': 1,
                            'uom_id': uom_id,
                            'under_warranty_bool': under_warranty,
                            'standard_price': standard_price,
                            'vat': vat_amount,
                            'on_hand_qty': on_hand_qty,
                            'overall_qty': overall_qty,
                            'parts_reserved_bool': parts_reserved_bool,
                        }
                        if service_lines:
                            service_lines[0].write(vals)
                        else:
                            # Remove any existing service lines first (clean slate)
                            if service_lines:
                                rec.product_line_ids = [(3, line.id, 0) for line in service_lines]
                            # Create new service line
                            rec.product_line_ids = [(0, 0, vals)]
                        # if self.inspection_charges_amount > 0:
                        #     self.send_whatsapp_service_charges_receipt()

            '''Code is added on Sep-05-2025 client asked the create the payment receipt based on the mode of payment check box and inspection charges amount '''
            if (vals.get('mode_of_payment') or vals.get('inspection_charges_amount')) or vals.get(
                    'inspection_charges_bool') == True:
                if record.mode_of_payment and record.inspection_charges_bool and record.inspection_charges_amount > 0.0:
                    if not record.team_id:
                        raise ValidationError("Please enter Team Leader")
                    if not record.planned_date_begin:
                        raise ValidationError("Please enter Appt. Start Date & Time")

                    payment_receipt_search = self.env['payment.receipt']
                    journal = False

                    if vals.get('mode_of_payment') == 'cash' or record.mode_of_payment == 'cash':
                        journal = self.env['account.journal'].search([('type', '=', 'cash')], limit=1)
                    else:
                        journal = self.env['account.journal'].search([('type', '=', 'bank')], limit=1)
                    payment_method_id = journal.inbound_payment_method_line_ids[
                        0].id if journal.inbound_payment_method_line_ids else False
                    payment_amount = vals.get('inspection_charges_amount') if vals.get(
                        'inspection_charges_amount') else record.inspection_charges_amount
                    currency = self.env.company.currency_id
                    job_search = self.env['project.task'].search([('name', '=', record.name)], limit=1)
                    vals_search = {
                        'date': fields.date.today(),
                        'job_card_no_id': job_search.id,
                        'partner_id': record.partner_id.id or '',
                        'customer_name': record.customer_name or '',
                        'amount': payment_amount,
                        'journal_id': journal.id,
                        'payment_id': payment_method_id,
                        'state': 'posted',
                        'memo': f'Inspection Charges Amount Received for {record.name} - {payment_amount:.2f} {currency.symbol}',
                        'inspection_charges_amount_received_bool': True,
                        'balance_amount_received_bool': False,
                        'mode_of_payment': record.mode_of_payment,
                        'online_transaction_date': fields.Datetime.now(),
                        'online_transaction_status': 'paid',

                    }
                    receipt_transaction = payment_receipt_search.search([('job_card_no_id.name', '=', record.name),
                                                                         (
                                                                         'inspection_charges_amount_received_bool', '=',
                                                                         True),
                                                                         ('balance_amount_received_bool', '=', False)
                                                                         ], limit=1)

                    if not receipt_transaction:
                        receipt_create = self.env['payment.receipt'].sudo().create(vals_search)
                        record.payment_receipt_id = receipt_create.id
                        if record.payment_receipt_id:
                            journal_entry = self.env['account.move']

                            journal_vals = {

                                'move_type': 'entry',
                                # 'account_id': receipt_create.journal_id,
                                # 'amount' :payment_amount,
                                'ref': receipt_create.name,
                                'date': receipt_create.date or False,
                                'journal_id': journal.id,
                            }

                            debit_account = receipt_create.journal_id.profit_account_id.id
                            credit_account = receipt_create.journal_id.loss_account_id.id
                            line_vals = []
                            debit_vals = {
                                'name': receipt_create.name,
                                'account_id': debit_account,
                                'journal_id': journal.id,
                                'debit': payment_amount,
                                'credit': 0.0,
                                'date': receipt_create.date

                            }

                            credit_vals = {
                                'name': receipt_create.name,
                                'account_id': credit_account,
                                'journal_id': journal.id,
                                'debit': 0.0,
                                'credit': payment_amount,
                                'date': receipt_create.date

                            }

                            line_vals.append((0, 0, debit_vals))
                            line_vals.append((0, 0, credit_vals))

                            transaction = journal_entry.sudo().create(journal_vals)
                            transaction.update({'line_ids': line_vals})
                            record.payment_receipt_id.write({'account_move_id': transaction.id})

                    if receipt_transaction:
                        inspection_amount = vals.get('inspection_charges_amount') if vals.get(
                            'inspection_charges_amount') else record.inspection_charges_amount
                        payment_mode = vals.get('mode_of_payment') if vals.get(
                            'mode_of_payment') else record.mode_of_payment
                        receipt_transaction.write({
                            'amount': inspection_amount,
                            'memo': f'Inspection Charges Amount Received for {record.name} - {inspection_amount:.2f} {currency.symbol}',
                            'mode_of_payment': payment_mode,
                            'journal_id': journal.id

                        })

            '''Code is added on Sep-05-2025 client asked the create the payment receipt based on the mode of balance payment check box and remaining balance paid amount '''

            if vals.get('mode_of_payment_balance_amount') or vals.get('balance_amount_received_bool') == True:
                balance_paid = False
                balance_paid = (record.grand_total - record.final_inspection_charges_amount)
                if record.mode_of_payment_balance_amount and record.balance_amount_received_bool and balance_paid > 0.0:
                    if not record.team_id:
                        raise ValidationError("Please enter Team Leader")
                    if not record.planned_date_begin:
                        raise ValidationError("Please enter Appt. Start Date & Time")

                    payment_receipt_search = self.env['payment.receipt']
                    journal = False
                    if vals.get(
                            'mode_of_payment_balance_amount') == 'cash' or record.mode_of_payment_balance_amount == 'cash':
                        journal = self.env['account.journal'].search([('type', '=', 'cash')], limit=1)
                    else:
                        journal = self.env['account.journal'].search([('type', '=', 'bank')], limit=1)
                    payment_method_id = journal.inbound_payment_method_line_ids[
                        0].id if journal.inbound_payment_method_line_ids else False
                    # payment_amount = vals.get('balance_paid')  if vals.get('balance_paid') else record.balance_paid
                    payment_amount = balance_paid
                    currency = self.env.company.currency_id
                    job_search = self.env['project.task'].search([('name', '=', record.name)], limit=1)
                    vals_search = {

                        'date': fields.date.today(),
                        'job_card_no_id': job_search.id,
                        'partner_id': record.partner_id.id or '',
                        'customer_name': record.customer_name or '',
                        'amount': payment_amount,
                        'journal_id': journal.id,
                        'payment_id': payment_method_id,
                        'state': 'posted',
                        'memo': f'Balance Amount Received for {record.name} - {payment_amount:.2f} {currency.symbol}',
                        'inspection_charges_amount_received_bool': False,
                        'balance_amount_received_bool': True,
                        'mode_of_payment': record.mode_of_payment,
                        'online_transaction_date': fields.Datetime.now(),
                        'online_transaction_status': 'paid',

                    }
                    receipt_transaction = payment_receipt_search.search([('job_card_no_id.name', '=', record.name),
                                                                         (
                                                                         'inspection_charges_amount_received_bool', '=',
                                                                         False),
                                                                         ('balance_amount_received_bool', '=', True)
                                                                         ], limit=1)

                    if not receipt_transaction:
                        receipt_create = self.env['payment.receipt'].sudo().create(vals_search)
                        record.payment_receipt_id = receipt_create.id
                        if record.payment_receipt_id:
                            journal_entry = self.env['account.move']

                            journal_vals = {

                                'move_type': 'entry',
                                # 'account_id': receipt_create.journal_id,
                                # 'amount' :payment_amount,
                                'ref': receipt_create.name,
                                'date': receipt_create.date or False,
                                'journal_id': journal.id,
                            }

                            debit_account = receipt_create.journal_id.profit_account_id.id
                            credit_account = receipt_create.journal_id.loss_account_id.id
                            line_vals = []
                            debit_vals = {
                                'name': receipt_create.name,
                                'account_id': debit_account,
                                'journal_id': journal.id,
                                'debit': payment_amount,
                                'credit': 0.0,
                                'date': receipt_create.date

                            }

                            credit_vals = {
                                'name': receipt_create.name,
                                'account_id': credit_account,
                                'journal_id': journal.id,
                                'debit': 0.0,
                                'credit': payment_amount,
                                'date': receipt_create.date

                            }

                            line_vals.append((0, 0, debit_vals))
                            line_vals.append((0, 0, credit_vals))

                            transaction = journal_entry.sudo().create(journal_vals)
                            transaction.update({'line_ids': line_vals})
                            record.payment_receipt_id.write({'account_move_id': transaction.id})

                    if receipt_transaction:
                        # balance_paid = vals.get('balance_paid') if vals.get('balance_paid') else record.balance_paid
                        payment_mode = vals.get('mode_of_payment_balance_amount') if vals.get(
                            'mode_of_payment_balance_amount') else record.mode_of_payment_balance_amount
                        receipt_transaction.write({
                            'amount': abs(balance_paid),
                            'memo': f'Balance Amount Received for {record.name} - {balance_paid:.2f} {currency.symbol}',
                            'mode_of_payment': payment_mode,
                            'journal_id': journal.id
                        })

        # if warnings:
        #     self.message_post(
        #         body="Stock Warning: " + "\n".join(warnings),
        #         message_type='notification',
        #         # subtype_xmlid='mail.mt_comment',
        #     )
        #
        # # Return client-side notification
        # if warning_needed:
        #     product_names = [line.product_id.display_name for rec in self for line in rec.line_ids if line.on_hand_qty == 0.0]
        #     return {
        #         'type': 'ir.actions.client',
        #         'tag': 'reload',  # triggers form reload and context refresh
        #         'context': {
        #             'show_stock_warning': True,
        #             'warning_products': ', '.join(product_names),
        #         },
        #     }
        #

        # self.action_save()

        # if state_changing_to_124:
        #     return self.cancelled_reason_button_mobile()
        #
        # if self.env.context.get('open_cancelled_wizard'):
        #     return self.cancelled_reason_button_mobile()
        #

        return res

    # def write(self, vals):
    #     # Perform the write operation
    #     res = super(ProjectTask, self).write(vals)
    #     if 'job_state' in vals:
    #         state = self.env['project.task.type'].sudo().browse(vals['job_state'])
    #         if state.code =='124':
    #             return {
    #
    #         'type':'ir.actions.act_window',
    #         'res_model':'cancelled.reason.wizard',
    #         'name' : 'Cancelled Reason',
    #         'view_mode':'form',
    #         'views': [(False, 'form')],
    #         'target': 'new',
    #         'context': {
    #             'default_job_card_id': self.id,
    #         }
    #         }
    #     return res

    # @api.onchange('job_card_state_code','job_state')
    # def _onchange_job_card(self):
    #     for rec in self:
    #         if rec.job_card_state_code == '126':
    #             print("...........................1111111onchnage")
    #             return {
    #                 'warning': {
    #                     'title': _("Warning"),
    #                     'message': _("If the date is set in the 'past, orders placed on this Amazon "
    #                                  "Account before the first synchronization of the module might be "
    #                                  "synchronized with Odoo.\n"
    #                                  "If the date is set in the future, orders placed on this Amazon "
    #                                  "Account between the previous and the new date will not be "
    #                                  "synchronized with Odoo.")
    #                 }
    #             }

    # @api.constrains('job_card_state_code', 'job_card_state')
    # def _check_job_card_state_code_valid(self):
    #     for rec in self:
    #         if rec._origin and rec.job_state != rec._origin.job_state:
    #
    #             if rec.job_card_state_code == '126':
    #                 if rec.product_line_ids:
    #                     for line in rec.product_line_ids:
    #                         if self.env['ir.config_parameter'].sudo().get_param('machine_repair_management.negative_stock_allow') == 'True':
    #                             if line.on_hand_qty == 0.0:
    #                                 return {
    #                                 'warning': {
    #                                     'title': 'Warning',
    #                                     'message': 'Stock is not available for this product'
    #                                 }
    #                             }
    #                             # raise ValidationError("Please Stock is not available.Please Contact Administrator")

    # @api.constrains('control_card_no', 'job_card_state_code')
    # def _check_control_card_no(self):
    #     for rec in self:
    #         if rec.job_card_state_code == '126' and not rec.control_card_no:
    #             raise ValidationError("Please enter 'Control Card No' in the Job Card.")
    #

    # @api.constrains('closed_datetime', 'job_card_state_code')
    # def _check_closed_datetime_validation(self):
    #     for rec in self:
    #
    #         if self.env.user.has_group('machine_repair_management.group_job_card_back_office_user'):
    #             if rec.job_card_state_code == '125' and not rec.closed_datetime:
    #                 raise ValidationError("Please enter 'Closed Date & Time'.")
    #

    # @api.constrains('team_id','job_card_state_code')
    # def _check_team_id_valid(self):
    #     for rec in self:
    #         if rec.job_card_state_code == '102':
    #             if not rec.team_id:
    #                 raise ValidationError("Please enter Technician in the Job card")
    #

    # @api.constrains('job_card_completed_time','job_card_state_code')
    # def _check_job_card_completed_date_time(self):
    #     for rec in self:
    #         if rec.job_card_state_code == '126' and not rec.job_card_completed_time:
    #             raise ValidationError("Please enter Job Card Completed Time in the Job card")
    #
    #

    # @api.constrains('engineer_comments', 'job_card_state_code')
    # def _engineer_comments_check(self):
    #     for rec in self:
    #         if rec.job_card_state_code == '124':
    #             if not rec.engineer_comments:
    #                 raise ValidationError("Please enter Engineer Comments in the Job Card")
    #

    # @api.constrains('job_card_state_code', 'job_card_state')
    # def _check_job_card_state_code_valid(self):
    #     for rec in self:
    #         if rec._origin and rec.job_state != rec._origin.job_state:
    #
    #             if rec.job_card_state_code in ('122', '126'):
    #                 if rec.product_line_ids:
    #                     for line in rec.product_line_ids:
    #                         if line.product_id:
    #                             if not line.parts_reserved_bool:
    #                                 raise ValidationError("Please check all the Products should be Reserved.This Product %s is not reserved" % line.product_id.display_name)
    #                         if line.on_hand_qty == 0.0:
    #                             raise ValidationError("Please Stock is not available.Please Contact Administrator")
    #
    #                 if not rec.product_line_ids:
    #                     raise ValidationError("Please give any one of the Product in the product consume Part/services")
    #
    #                     # elif rec.quotation_count == 0:
    #                     #     # if not rec.sale_id:
    #                     #     if rec.product_line_ids:
    #                     #         for line in rec.product_line_ids:
    #                     #             if not line.under_warranty_bool:
    #                     #                 raise ValidationError("Complete your quotation first, then close the job card")
    #                     #
    #                     if self.product_line_ids:
    #                         if self.inspection_charges_bool and self.inspection_charges_amount > 0:
    #                             if not any(line.product_id and line.product_id.service_type_bool for line in self.product_line_ids):
    #                                 raise ValidationError("Please enter service charge amount in the product line")
    #
    #             # elif rec.job_card_state_code not in ('101','102'):
    #             #     if not rec.team_id :
    #             #         raise ValidationError("Please give the Team Leader Name")
    #             #     if not rec.technician_id:
    #             #         raise ValidationError("Please Enter Technician Name ")
    #             #

    #### this is working when state is changed in the job card then related state is chned in the service request
    # @api.model
    # def write(self, vals):
    #     res = super(ProjectTask, self).write(vals)
    #     if 'job_state' in vals:
    #         for task in self:
    #             if task.service_request_id:
    #                 task.service_request_id.write({
    #                     'state': vals['job_state']
    #                 })
    #     return res

    @api.model
    def default_get(self, fields):
        res = super().default_get(fields)

        # Set the default project_id if it's provided (or fetched dynamically)
        if self.project_id:
            project = self.env['project.project'].browse(self.project_id.id)
            fallback_state = self.env['project.task.type'].search([('project_ids', '=', project.id)], limit=1)
            if fallback_state:
                res['job_state'] = fallback_state.id
        return res

    # Service Info
    service_nature_id = fields.Many2one('service.nature', string="Service Type")
    # name = fields.Char(string="Job Card #", )
    # name = fields.Char(string="Job Card #", required=True,
    #                    default=lambda self: self.env['ir.sequence'].next_by_code('project_task.sequence'))

    location_id = fields.Many2one('hr.work.location', string="Location", )
    control_card_no = fields.Char(string="Control Card No")
    warehouse_id = fields.Many2one('stock.warehouse', string="Warehouse")
    # technician_id = fields.Many2one('res.users', string="Technician Name")
    service_created_datetime = fields.Datetime(string="Service Created Date & Time")
    service_requested_datetime = fields.Datetime(string="Service Requested Appt Date & Time")
    """service_requested_datetime = fields.Datetime(string="Requested Date & Time")"""
    appointment_datetime = fields.Datetime(string="Actual Appt Date & Time")
    closed_datetime = fields.Datetime(string="Completed Date & Time",
                                      help="Actual Technician closed the Job card ie)Ready to invoice ")
    job_card_completed_time = fields.Datetime(string="Job Card Closed Date & Time",
                                              help="Overall Supervisor closed Job card")
    rtat_hours = fields.Float(string="RTAT", compute='_compute_rtat_hours', store=True)

    # Customer
    partner_id = fields.Many2one('res.partner', string="Customer Name")
    phone = fields.Char(string="Mobile No", readonly=True)
    address = fields.Char(string="Address", store=True, compute="_compute_address")
    latitude = fields.Char(string="Latitude", store=True)
    longitude = fields.Char(string="Longitude", store=True)

    # address = fields.Char(string="Address", compute="_compute_address", store=True)
    # latitude = fields.Char(string="latitude", compute="_compute_address", store=True)
    # longitude = fields.Char(string="longitude", compute="_compute_address", store=True)

    # Product Info
    # product_category_id = fields.Many2one('product.category', string="Product Category", required=True)
    product_category_id = fields.Many2one(
        'product.category',
        string="Product Category",
        domain="[('parent_id','=',False),('name', '!=', 'All')]"
    )
    # product_category_id = fields.Many2one(
    #     'product.category',
    #     string="Product Category",
    #     required=True,
    #     domain=lambda self: self._get_valid_product_category_domain()
    # )
    #
    # @api.model
    # def _get_valid_product_category_domain(self):
    #     all_categories = self.env['product.category'].search([('name', '!=', 'All')])
    #     valid_categories = all_categories.filtered(lambda c: not c.parent_id or c.parent_id.name != 'All')
    #     return [('id', 'in', valid_categories.ids)]

    product_id = fields.Many2one('product.product', string="Model No",
                                 )
    brand = fields.Char(string="Brand")
    model = fields.Char(string="Model")
    # product_slno = fields.Char(string="Serial Number")
    product_slno = fields.Char(string="Serial Number", store=True)

    # Purchase Info
    purchase_invoice_no = fields.Char(string="Purchase Invoice Number")
    purchase_date = fields.Date(string="Purchase Date")
    # purchase_dealer_name = fields.Char(string="Dealer Name",deprecated=False)
    dealer_id = fields.Many2one('res.partner', string="Dealer Name",
                                domain="[('partner_type_hhs','=','customer'),('sub_partner_type','=','dealer')]")

    warranty = fields.Boolean(string="Warranty", default=False)
    warranty_expiry_date = fields.Date(string="Warranty Expiry Date", store=True)

    symptoms_line_ids = fields.One2many('project.task.symptoms', 'project_task_id', string="Symptoms")
    defects_type_ids = fields.One2many('project.task.defects', 'project_task_id', string="Defects")
    service_type_ids = fields.One2many('project.task.service', 'project_task_id', string="Service")
    timesheet_line_ids = fields.One2many('account.analytic.line', 'project_request_id', string='Timesheets')
    product_line_ids = fields.One2many('product.lines', 'project_task_id', string='Product Lines')

    # Duplicate One2many fields
    symptoms_line_ids_duplicate = fields.One2many('project.task.symptoms', 'project_task_id',
                                                  string="Symptoms Duplicate")
    defects_type_ids_duplicate = fields.One2many('project.task.defects', 'project_task_id', string="Defects Duplicate")
    service_type_ids_duplicate = fields.One2many('project.task.service', 'project_task_id', string="Service Duplicate")
    product_line_ids_duplicate = fields.One2many('product.lines', 'project_task_id', string='Product Lines Duplicate')

    client_comments = fields.Text(string="Client Comments")

    technician_comments = fields.Text(string="Technician Comments")

    engineer_comments = fields.Text(string="Technician Comments")

    grand_total = fields.Float(string='Grand Total', compute='_compute_grand_total', store=True)

    call_date = fields.Date(
        string='Call Date',
        compute='_compute_job_request_date_time',
    )

    call_time = fields.Char(
        string='Call Time',
        compute='_compute_job_request_date_time',
    )
    appt_date = fields.Date(
        string='Actual App Date',
        compute='_compute_job_appointment_datetime', store=True
    )

    appt_time = fields.Char(
        string='Actual App Time',
        compute='_compute_job_appointment_datetime', store=True
    )
    closed_date = fields.Date(
        string='Completed Date',
        compute='_compute_job_close_datetime', store=True
    )

    closed_time = fields.Char(
        string='Completed Time',
        compute='_compute_job_close_datetime', store=True
    )

    service_request_date = fields.Date(string="Service Req.Appt.Date",
                                       compute="_compute_service_requested_date", store=True)

    service_request_time = fields.Char(string="Service Req.Appt Time",
                                       compute="_compute_service_requested_date", store=True)

    district = fields.Char(string='District')
    check_user = fields.Boolean(string='User', compute='_compute_user_check', default=False)

    scheduled_date = fields.Datetime('Scheduled Date', default=fields.Datetime.now)
    technician_accepted_date = fields.Datetime('Technician Accepted Date')
    technician_rejected_date = fields.Datetime('Technician Rejected Date')
    technician_started_date = fields.Datetime('Technician Started Date')
    technician_reached_date = fields.Datetime('Technician Reached Date')
    job_started_date = fields.Datetime('Job Started Date')
    job_hold_date = fields.Datetime('Job Hold Date')
    job_resume_date = fields.Datetime('Job Resume Date')
    job_other1_date = fields.Datetime('Job Other1 Date')
    job_other2_date = fields.Datetime('Job Other2 Date')
    job_other3_date = fields.Datetime('Job Other3 Date')
    job_other4_date = fields.Datetime('Job Other4 Date')
    job_other5_date = fields.Datetime('Job Other5 Date')

    invoice_no = fields.Char(string='Invoice No')

    payment_receipt_id = fields.Many2one('payment.receipt', string="Payment receipt")

    payment_receipt_count = fields.Integer(string='Payment Receipt Count', compute="_compute_payment_receipt_count")

    quotation_count = fields.Integer(string="Quotation Count", compute="_compute_quotation_count")

    supervisor_comments = fields.Text(string="Supervisor/Inventory Controller Comments")

    cancel_date_time = fields.Datetime(string="Cancel Date Time")

    client_remarks = fields.Text(string="Client Remarks")

    # engineer_comments = fields.Text(string="Technician Comments")

    service_call_center_comments = fields.Text(string="Call Center comments")

    job_card_partner_city = fields.Char(string="City")

    service_warranty_amount = fields.Float(string="Service Warranty Amount", store=True,
                                           compute="_compute_service_warranty_amount")

    warehouse_lst_ids = fields.Many2many('stock.warehouse', store=True, compute="_compute_warehouse_lst_ids")

    # available_state_ids = fields.Many2many('project.task.type', store = True )

    available_state_ids = fields.Many2many('project.task.type', compute="_compute_available_state_ids", store=False)

    sale_id = fields.Many2one('sale.order', store=True, string="Sale Order")

    service_sale_id = fields.Many2one('service.sale.order', string="Sale Order", store=True)

    ''' If sale order is cancelled then only create quotation button is enabled added on May 21 2025'''
    sale_order_state_check = fields.Boolean(string="Sale order Check", default=False,
                                            compute="_compute_sale_order_state_check")

    inspection_charges_amount = fields.Float(string="Inspection Charges Amount(Inc.VAT)", store=True)

    inspection_charges_bool = fields.Boolean(string="Inspection Charges Bool", default=True)

    final_inspection_charges_amount = fields.Float(string="Amount Received",
                                                   compute="_compute_final_inspection_charges_amount", store=True)

    balance_amount_received_bool = fields.Boolean(string="Balance Amount Confirmed", default=False)

    balance_amount_received = fields.Float(string="Balance Amount Received")

    balance_paid = fields.Float(string="Balance To Be Paid", compute="_compute_grand_total", store=True)

    ''' this code is commented by Vijaya bhaskar on July 17 2025 because client client asked don't need inspection charges amount
    inspection_charges_amount = fields.Float(string = "Inspection Charges Amount" , store = True,compute="_compute_inspection_charges_amount", default = lambda self: float(self.env['ir.config_parameter'].sudo().get_param('machine_repair_management.inspection_amount'))) 
    '''
    '''This code is added on June 12 for if product is not in the product consume parts/services then print service receipt is not enabled  and it is also used for whatsapp send the print service receipt(print_job_card_receipt)'''
    product_line_ids_check = fields.Boolean(string="Product Lines", default=False, store=True,
                                            compute="_compute_product_line_ids")

    invoice_no_check = fields.Boolean(string="Invoice no check", default=False, store=True,
                                      compute="_compute_invoice_no")

    inspection_charges_receipt_click = fields.Boolean(string="Inspection Charges Receipt click", default=False)

    service_charge_receipt_print_click = fields.Boolean(string="Service Charge Receipt click", default=False)

    invoice_receipt_print_click = fields.Boolean(string="Invoice Receipt Click ", default=False)

    whatsapp_receipt_sent = fields.Boolean('WhatsApp Receipt Sent', default=False)

    whatsapp_invoice_sent = fields.Boolean('WhatsApp Invoice Sent', default=False, store=True)

    import_bool = fields.Boolean(string='Import', default=False)

    img1 = fields.Binary(
        string="Images1",
    )
    img2 = fields.Binary(
        string="Images2",
    )
    img3 = fields.Binary(
        string="Images3",
    )
    img4 = fields.Binary(
        string="Images4",
    )
    img5 = fields.Binary(
        string="Images5",
    )

    img1_text = fields.Text(string="Image 1 Text")
    img2_text = fields.Text(string="Image 2 Text")
    img3_text = fields.Text(string="Image 3 Text")
    img4_text = fields.Text(string="Image 4 Text")
    img5_text = fields.Text(string="Image 5 Text")

    signature = fields.Binary(string="Customer Signature")

    customer_city_id = fields.Many2one('res.city', string="City")

    country_district_id = fields.Many2one('res.state.district', string="District")

    country_state_id = fields.Many2one("res.country.state", string='State', ondelete='restrict',
                                       domain="[('country_id', '=?', country_id)]")

    country_id = fields.Many2one('res.country', string="Country")

    zip_code = fields.Char(string='Zip code')

    customer_name = fields.Char(string="Customer name")

    customer_identification_scheme = fields.Selection([
        ('TIN', 'Tax Identification Number'),
        ('CRN', 'Commercial Registration Number'),
        ('IQA', 'Iqama Number'),
        ('NAT', 'National ID'),
    ], string="Identification Scheme", help="Additional Identification scheme for Seller/Buyer")

    customer_identification_number = fields.Char("VAT No",
                                                 help="Additional Identification Number for Seller/Buyer")

    whatsapp_opt_in = fields.Boolean(string="Whatsapp", default=True)

    building_number = fields.Char("Building Number")

    plot_identification = fields.Char("Plot Identification")

    partner_latitude = fields.Float(string='Latitude', digits=(10, 7))

    partner_longitude = fields.Float(string='Longitude', digits=(10, 7))

    address_one = fields.Char(string="Address 1")

    address_two = fields.Char(string="Address 2")

    email = fields.Char(
        string="Email",
        required=False
    )

    service_warranty_id = fields.Many2one('service.warranty', string="Service Warranty")

    product_group_id = fields.Many2one('product.category', string="Product Group",
                                       domain="[('parent_id','=',product_category_id)]",
                                       context=lambda self: {'show_only_name': True})

    product_sub_group_id = fields.Many2one('product.category', string="Product Sub Group",
                                           domain="[('parent_id','=',product_group)]",
                                           name_field='name', context=lambda self: {'show_only_name': True})

    attachment_ids = fields.Many2many('ir.attachment', string="Attachment",
                                      help="Multiple Images and Pdf is attached here",
                                      domain="[('mimetype','in',['image/jpeg','image/png','image/gif','application/pdf'])]")

    # maintenance_tab_show_bool = fields.Boolean(default = _default_maintenance_tab_show_bool)

    maintenance_tab_show_bool = fields.Boolean(string="Maintenance Tab show", compute="_compute_maintenance_tab")

    mode_of_payment = fields.Selection([('cash', 'Cash'), ('online', 'Online Payment'),
                                        ('bank', 'Bank Transfer'), ('credit', 'Credit')], string="Method of Payment")

    mode_of_payment_balance_amount = fields.Selection([('cash', 'Cash'), ('online', 'Online Payment'),
                                                       ('bank', 'Bank Transfer'), ('credit', 'Credit')],
                                                      string="Method of Payment")

    duplicate_service_button_clicked = fields.Boolean(string="Duplicate service button click", default=False,
                                                      help="After click the Create New service Request button then the button is disable")

    job_card_closed_date_time_enable = fields.Boolean(string="Job Card Completed Time Enable", default=False,
                                                      compute="_compute_job_card_closed_date_time_enable")

    cancellation_reason_id = fields.Many2one('cancellation.reason', string="Cancellation Reason")

    whatsapp_send_bool = fields.Boolean(string="Whatsapp Send Y/N", default=False,
                                        help="All Whatsapp Send feature Enable/Not in res.config_settings",
                                        compute="_compute_whatsapp_send_bool")

    def _compute_whatsapp_send_bool(self):
        for rec in self:
            rec.whatsapp_send_bool = False
            whatsapp_search = self.env['ir.config_parameter'].sudo().get_param(
                'machine_repair_management.whatsapp_send_bool')
            if whatsapp_search == 'True':
                rec.whatsapp_send_bool = True

    '''Client asked job card closed date time enable/disable based on user settings added on Sep 11 -2025 by Vijaya Bhaskar'''

    def _compute_job_card_closed_date_time_enable(self):

        for rec in self:
            rec.job_card_closed_date_time_enable = False
            job_card_closed_search = self.env['ir.config_parameter'].sudo().get_param(
                'machine_repair_management.job_card_closed_time_enable')
            if job_card_closed_search == 'True':
                rec.job_card_closed_date_time_enable = True

    @api.depends('phone')
    def _compute_maintenance_tab(self):
        for rec in self:
            rec.maintenance_tab_show_bool = False
            maintenance_search = self.env['ir.config_parameter'].sudo().get_param(
                'machine_repair_management.maintenance_service_show')
            if maintenance_search == 'True':
                rec.maintenance_tab_show_bool = True

    google_maps_url = fields.Char("Google Maps URL")

    # latitude = fields.Char("Latitude", readonly=True)
    # longitude = fields.Char("Longitude", readonly=True)

    def update_google_map(self):
        # @api.onchange('google_maps_url')
        # def _onchange_google_maps_url(self):
        for rec in self:
            lat, lng = rec.extract_lat_long(rec.google_maps_url)
            rec.latitude = lat or ''
            rec.longitude = lng or ''
            rec.partner_latitude = lat or ''
            rec.partner_longitude = lng or ''
            rec.service_request_id.partner_latitude = lat or False
            rec.service_request_id.partner_longitude = lng or False
            rec.partner_id.partner_latitude = lat or False
            rec.partner_id.partner_longitude = lng or False

    # def extract_lat_long(self, google_maps_url):
    #     """
    #     Extract latitude and longitude from a Google Maps URL.
    #     Supports format like: https://maps.google.com/maps?q=LAT%2CLONG...
    #     """
    #     try:
    #         parsed_url = urlparse(google_maps_url)
    #         query_params = parse_qs(parsed_url.query)
    #
    #         if 'q' in query_params:
    #             coords = unquote(query_params['q'][0]).split(',')
    #             if len(coords) == 2:
    #                 return coords[0].strip(), coords[1].strip()
    #     except Exception:
    #         pass
    #     return None, None

    def extract_lat_long(self, google_maps_url):
        """
        Extract latitude and longitude from multiple Google Maps URL formats:
        - ?q=lat,lng
        - /@lat,lng,...
        - /place/lat,lng
        - /dir/.../lat,lng
        """
        try:
            if not google_maps_url:
                return None, None

            parsed_url = urlparse(google_maps_url)
            query_params = parse_qs(parsed_url.query)

            # Format 1: https://maps.google.com/maps?q=lat,lng
            if 'q' in query_params:
                coords = unquote(query_params['q'][0]).split(',')
                if len(coords) == 2:
                    return coords[0].strip(), coords[1].strip()

            # Format 2: /@lat,lng,...
            if '/@' in parsed_url.path:
                at_part = parsed_url.path.split('/@')[1]
                coords = at_part.split(',')[:2]
                if len(coords) == 2:
                    return coords[0].strip(), coords[1].strip()

            # Format 3: /place/lat,lng or /dir/.../lat,lng
            path_parts = parsed_url.path.split('/')
            for part in path_parts:
                if ',' in part:
                    coords = part.split(',')
                    if len(coords) >= 2:
                        lat = coords[0].strip()
                        lng = coords[1].strip()
                        # Validate they are float-like
                        try:
                            float(lat)
                            float(lng)
                            return lat, lng
                        except ValueError:
                            continue
        except Exception:
            pass

        return None, None

    # @api.depends('balance_amount_received_bool','grand_total','inspection_charges_amount')
    # def _compute_balance_paid(self):
    #     for rec in self:
    #         if rec.balance_amount_received_bool and rec.inspection_charges_amount > 0 :
    #             rec.balance_paid = rec.grand_total - rec.inspection_charges_amount
    #

    @api.onchange('product_category_id', 'product_group_id', 'product_sub_group_id', 'product_id')
    def _onchange_product_group(self):
        for rec in self:
            if rec.service_request_id:
                if rec.product_category_id:
                    rec.service_request_id.product_category = rec.product_category_id.id or None,
                if rec.product_group_id:
                    rec.service_request_id.product_group_id = rec.product_group_id.id or None
                if rec.product_sub_group_id:
                    rec.service_request_id.product_sub_group_id = rec.product_sub_group_id.id or None
                if rec.product_id:
                    rec.service_request_id.product_id = rec.product_id.id or None

    @api.depends('inspection_charges_amount', 'inspection_charges_bool')
    def _compute_final_inspection_charges_amount(self):
        for rec in self:
            rec.final_inspection_charges_amount = False
            if rec.inspection_charges_amount and rec.inspection_charges_bool:
                rec.final_inspection_charges_amount = rec.inspection_charges_amount

    # @api.onchange('inspection_charges_amount', 'inspection_charges_bool')
    # def _onchange_inspection_charges_amount(self):
    #     for rec in self:
    #         # Remove any existing service type products first to avoid duplicates
    #         service_lines = rec.product_line_ids.filtered(
    #             lambda line: line.product_id.service_type_bool == True
    #         )
    #
    #         # Remove all service lines first
    #         if service_lines:
    #             rec.product_line_ids = [(3, line.id) for line in service_lines]
    #
    #         # Only add service product if conditions are met
    #         if rec.inspection_charges_amount > 0 and rec.inspection_charges_bool and rec.warehouse_id:
    #             stock_quant_search = self.env['stock.quant'].search([
    #                 ('product_id.service_type_bool', '=', True),
    #                 ('location_id', '=', rec.warehouse_id.lot_stock_id.id)
    #             ], limit=1)
    #
    #             if stock_quant_search:
    #                 vals = {
    #                     'product_id': stock_quant_search.product_id.id,
    #                     'qty': 1.0,
    #                 }
    #                 # Add the service product line
    #                 rec.product_line_ids = [(0, 0, vals)]
    #
    #                 # Get the newly added line and set its price
    #                 new_line = rec.product_line_ids.filtered(
    #                     lambda line: line.product_id.id == stock_quant_search.product_id.id
    #                 )
    #                 if new_line:
    #                     new_line._product_line_onchange()
    #                     # Calculate base price (excluding VAT)
    #                     if new_line.vat and new_line.vat > 0:
    #                         base_price = rec.inspection_charges_amount / (1 + (new_line.vat / 100))
    #                     else:
    #                         base_price = rec.inspection_charges_amount
    #                     new_line.price_unit = base_price
    #

    # @api.onchange('inspection_charges_bool', 'inspection_charges_amount')
    # def _onchange_inspection_charges_amount(self):
    #     if self.env.context.get('skip_state_validation'):
    #         return False
    #     for rec in self:
    #         print("333333333333333333333")
    #
    #         if rec.inspection_charges_amount > 0 and rec.inspection_charges_bool and rec.warehouse_id:
    #             # Clear existing service product lines only
    #             # service_lines = rec.product_line_ids.filtered(
    #             #     lambda line: line.product_id.service_type_bool
    #             # )
    #             # if service_lines:
    #             #     rec.product_line_ids = [(3, line.id, 0) for line in service_lines]
    #
    #             # Search for service product in warehouse
    #             print(".11111111111111111111111111111")
    #             stock_quant = self.env['stock.quant'].search([
    #                 ('product_id.service_type_bool', '=', True),
    #                 ('location_id', '=', rec.warehouse_id.lot_stock_id.id)
    #             ], limit=1)
    #
    #             if stock_quant:
    #                 product = stock_quant.product_id
    #                 price_unit = rec.inspection_charges_amount
    #                 vat_taxes = product.taxes_id
    #                 vat_amount = 0.0
    #                 if vat_taxes:
    #                     vat_amount = vat_taxes[0].amount
    #                     tax_factor = 1 + (vat_amount / 100)
    #                     price_unit /= tax_factor
    #
    #                 # Set additional fields similar to _product_line_onchange without overwriting price_unit
    #                 uom_id = product.uom_id.id
    #                 under_warranty = rec.warranty
    #                 standard_price = product.lst_price
    #                 on_hand_qty = stock_quant.quantity if stock_quant else 0.0
    #
    #                 quantity_search = self.env['stock.quant'].search([('product_id', '=', product.id)])
    #                 overall_qty = sum(quant.quantity for quant in quantity_search) if quantity_search else 0.0
    #
    #                 parts_reserved_bool = rec.warranty
    #
    #                 vals = {
    #                     'product_id': product.id,
    #                     'price_unit': price_unit,
    #                     'qty': 1,
    #                     'uom_id': uom_id,
    #                     'under_warranty_bool': under_warranty,
    #                     'standard_price': standard_price,
    #                     'vat': vat_amount,
    #                     'on_hand_qty': on_hand_qty,
    #                     'overall_qty': overall_qty,
    #                     'parts_reserved_bool': parts_reserved_bool,
    #                 }
    #                 rec.product_line_ids = [(0, 0, vals)]
    #                 print("...3222222222222222222222222222222222222222222")
    # else:
    #     # Remove service product lines if conditions not met
    #     service_lines = rec.product_line_ids.filtered(
    #         lambda line: line.product_id.service_type_bool
    #     )
    #     if service_lines:
    #         rec.product_line_ids = [(3, line.id, 0) for line in service_lines]

    @api.constrains('email')
    def _valid_check_email(self):
        for rec in self:
            if rec.email:
                if '@' not in rec.email or '.' not in rec.email:
                    raise ValidationError("Please enter a valid email address must contain @ and .")
                elif not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', rec.email):
                    raise ValidationError("Please enter a properly formatted email address")

    @api.constrains('building_number', 'plot_identification')
    def _check_building_number_job_card(self):
        for rec in self:
            if rec.building_number:
                if not rec.building_number.isdigit():
                    raise ValidationError("Please enter Building number is always number not character")
                if rec.building_number.isdigit():
                    if len(rec.building_number) != 5:
                        raise ValidationError("Building number  always 5 numbers")
            if rec.plot_identification:
                if not rec.plot_identification.isdigit():
                    raise ValidationError('Please enter Plot identification number is always number')
                if rec.plot_identification.isdigit():
                    if len(rec.plot_identification) != 5:
                        raise ValidationError("Plot identification Number always 5 digits")

    @api.onchange('customer_identification_scheme')
    def _onchange_customer_identification_scheme_job_card(self):
        for rec in self:
            if rec.customer_identification_scheme:
                if rec.customer_identification_scheme != 'TIN':
                    rec.customer_identification_number = None
                    rec.building_number = None
                    rec.plot_identification = None
                else:
                    if rec.partner_id.additional_identification_scheme == 'TIN':
                        rec.customer_identification_number = rec.partner_id.vat or None
                        rec.building_number = rec.partner_id.building_number or None
                        rec.plot_identification = rec.partner_id.plot_identification or None
            else:
                rec.customer_identification_number = None
                rec.building_number = None
                rec.plot_identification = None

    @api.depends('address_one', 'address_two', 'customer_city_id', 'country_district_id', 'country_state_id',
                 'country_id', 'zip_code')
    def _compute_address(self):
        for rec in self:
            # rec.address = False
            address_parts = [
                rec.address_one or False,
                rec.address_two or False,
                rec.customer_city_id.name or False,
                rec.country_district_id.name or False,
                rec.country_state_id.name or False,
                rec.country_id.name or False,
                rec.zip_code or False

            ]
            rec.address = ",".join(filter(None, address_parts))

    @api.depends('product_line_ids')
    def _compute_product_line_ids(self):
        for rec in self:
            rec.product_line_ids_check = False
            if rec.product_line_ids:
                rec.product_line_ids_check = True

    @api.depends('invoice_no')
    def _compute_invoice_no(self):
        for rec in self:
            rec.invoice_no_check = False
            if rec.invoice_no:
                rec.invoice_no_check = True

    @api.onchange('address_one', 'address_two', 'customer_city_id', 'country_district_id', 'country_state_id',
                  'zip_code', 'district', 'email', 'whatsapp_opt_in', 'customer_name',
                  'country_id', 'customer_identification_scheme', 'customer_identification_number', 'building_number',
                  'plot_identification', 'partner_latitude', 'partner_longitude')
    def _onchange_customer_name_info(self):
        for rec in self:

            if rec.service_request_id:
                rec.service_request_id.email = rec.email or False
                rec.service_request_id.address = rec.address or False
                rec.service_request_id.address_one = rec.address_one or False
                rec.service_request_id.address_two = rec.address_two or False
                rec.service_request_id.customer_city_id = rec.customer_city_id.id or False
                rec.service_request_id.country_district_id = rec.country_district_id.id or False
                rec.service_request_id.country_state_id = rec.country_state_id.id or None
                rec.service_request_id.country_id = rec.country_id.id or False
                rec.service_request_id.zip_code = rec.zip_code or False
                rec.service_request_id.customer_identification_scheme = rec.customer_identification_scheme or False
                rec.service_request_id.customer_identification_number = rec.customer_identification_number or False
                rec.service_request_id.whatsapp_opt_in = rec.whatsapp_opt_in or False
                rec.service_request_id.building_number = rec.building_number or False
                rec.service_request_id.plot_identification = rec.plot_identification or False
                rec.service_request_id.partner_latitude = rec.partner_latitude or False
                rec.service_request_id.partner_longitude = rec.partner_longitude or False
                rec.service_request_id.customer_name = rec.customer_name or None
                rec.service_request_id.partner_id = rec.partner_id.id or None

                address_parts = [
                    rec.building_number,
                    rec.plot_identification,
                    rec.address_one,
                    rec.address_two,
                    rec.zip_code,
                    rec.district,
                    rec.customer_city_id.name if rec.customer_city_id else "",
                    rec.country_state_id.name if rec.country_state_id else "",
                    rec.country_id.name if rec.country_id else "",
                ]
                full_address = ', '.join(filter(None, address_parts))
                if full_address:
                    try:
                        geolocator = Nominatim(user_agent="odoo_geolocator")
                        location = geolocator.geocode(full_address, timeout=10)
                        if location:
                            rec.partner_latitude = location.latitude
                            rec.partner_longitude = location.longitude
                    except Exception as e:
                        _logger.warning(f"GeoPy geocoding failed for '{full_address}': {e}")

                # rec.service_request_id._create_res_partner()

    ''' this code is commented by Vijaya bhaskar on July 17 2025 because client client asked don't need inspection charges amount
    # @api.onchange('warranty') 
    @api.depends('warranty')
    def _compute_inspection_charges_amount(self):
        for rec in self:
            if rec.warranty:
                rec.inspection_charges_amount = 0.0
            if not rec.warranty:
                rec.inspection_charges_amount = float(self.env['ir.config_parameter'].sudo().get_param('machine_repair_management.inspection_amount'))    
    '''

    @api.onchange('service_warranty_id')
    def _onchange_service_warranty_id(self):
        for rec in self:
            if rec.service_warranty_id:
                rec.warranty = rec.service_warranty_id.warranty_applicable_bool

                '''If Mis use warranty bool then warranty also tick code is added on Oct 17 -2025 '''
                if not rec.service_warranty_id.warranty_applicable_bool:
                    if rec.service_warranty_id.misuse_warranty_bool:
                        rec.warranty = True

                if not rec.warranty:
                    rec.inspection_charges_amount = float(
                        self.env['ir.config_parameter'].sudo().get_param('machine_repair_management.inspection_amount'))

                    # @api.constrains('product_id', 'job_card_state_code')

    # def _check_product_id_alias_model_no(self):
    #     for rec in self:
    #         if rec.job_card_state_code in ('125','126') and not rec.product_id:
    #             raise ValidationError("Please enter Model No. in the Job card")
    #
    #

    # @api.constrains('product_slno','job_card_state_code')
    # def _check_serial_number_mandatory(self):
    #     for rec in self:
    #         if rec.job_card_state_code == '125' and not rec.product_slno and rec.product_id:
    #             raise ValidationError("Please enter Serial Number in the Job card")
    #

    # @api.constrains('purchase_invoice_no', 'job_card_state_code','warranty')
    # def _check_purchase_invoice_purchase_number(self):
    #     for rec in self:
    #         if rec.job_card_state_code in ('125', '126') and rec.warranty and not rec.purchase_invoice_no and rec.product_slno and rec.product_id:
    #             # if rec.warranty:
    #             #     if not rec.purchase_invoice_no:
    #             raise ValidationError("Please enter Purchase Invoice No")
    #

    # @api.constrains('purchase_date', 'job_card_state_code','warranty')
    # def _check_purchase_invoice_purchase_date(self):
    #     for rec in self:
    #         if rec.job_card_state_code in ('125', '126') and rec.warranty and not rec.purchase_date  and rec.purchase_invoice_no and rec.product_slno and rec.product_id:
    #             # if rec.warranty:
    #             #     if not rec.purchase_date:
    #             raise ValidationError("Please enter Purchase date")
    #
    #

    @api.constrains('attachment_ids', 'job_card_state_code')
    def _attachment_ids_check(self):

        if self.env.context.get('skip_state_validation'):
            return False

        for rec in self:
            if self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
                ##commented on Sep 29-2025 due to client ask remove the document invoice
                # if rec.job_card_state_code in ('125','126'):
                #     if rec.warranty:
                #         if not rec.attachment_ids and  rec.purchase_invoice_no and rec.product_slno and rec.product_id and rec.purchase_date:
                #             raise ValidationError(_('Please attached some invoice Documents in Document & Invoice'))
                #

                allowed_mimetypes = ['image/jpeg', 'image/png', 'image/gif', 'application/pdf']
                for attachment in rec.attachment_ids:
                    if attachment.mimetype not in allowed_mimetypes:
                        raise ValidationError(_(
                            "Only PDF, JPG, PNG, and GIF files are allowed.\n"
                            f"Invalid file: {attachment.name}"
                        ))

    # @api.constrains('service_warranty_id', 'job_card_state_code')
    # def _check_service_warranty(self):
    #     for rec in self:
    #         if rec.job_card_state_code in ('125', '126'):
    #             if not rec.service_warranty_id:
    #                 raise ValidationError("Please select any one Service Warranty")
    #

    # @api.depends('sale_id')
    # def _compute_sale_order_state_check(self):
    #     for rec in self:
    #         rec.sale_order_state_check = False
    #         stage_model = self.env['project.task.type']
    #         if rec.sale_id:
    #             if rec.sale_id.state == 'cancel':
    #                 rec.sale_order_state_check = True

    @api.depends('service_sale_id')
    def _compute_sale_order_state_check(self):
        for rec in self:
            rec.sale_order_state_check = False
            if rec.service_sale_id:
                if rec.service_sale_id.state == 'cancel':
                    rec.sale_order_state_check = True

    @api.depends('team_id')
    def _compute_warehouse_lst_ids(self):
        for rec in self:
            warehouse_ids = []
            user_search = self.env['res.users'].search([('id', '=', rec.team_id.leader_id.id)], limit=1)
            if user_search.has_group('warehouse_restrictions_app.group_restrict_stock_warehouse'):
                if user_search.restrict_stock_warehouse_operation:
                    warehouse_ids.extend(user_search.available_warehouse_ids.ids)
                    rec.warehouse_id = user_search.property_warehouse_id.id or None
            else:
                warehouse_ids.extend(self.env['stock.warehouse'].search([]).ids)

            unique_warehouse_ids = list(set(warehouse_ids))  # Remove duplicates
            rec.warehouse_lst_ids = [(6, 0, unique_warehouse_ids)]

            # Get warehouses based on product category and work center

            # if rec.product_category_id and rec.work_center_id:
            #     for warehouse in rec.product_category_id.category_line_ids:
            #         if warehouse.work_center_location_id and warehouse.work_center_location_id == rec.work_center_id:
            #             warehouse_ids.append(warehouse.warehouse_id.id)
            #
            # # Add based on user group restrictions
            # if self.env.user.has_group('warehouse_restrictions_app.group_restrict_stock_warehouse'):
            #     if self.env.user.restrict_stock_warehouse_operation:
            #         warehouse_ids.extend(self.env.user.available_warehouse_ids.ids)
            # else:
            #     warehouse_ids.extend(self.env['stock.warehouse'].search([]).ids)
            #
            # # Remove duplicates and convert to a list of commands for Many2many
            # unique_warehouse_ids = list(set(warehouse_ids))  # Remove duplicates
            # rec.warehouse_lst_ids = [(6, 0, unique_warehouse_ids)]
            #

    @api.depends('product_line_ids')
    def _compute_service_warranty_amount(self):
        for rec in self:
            rec.service_warranty_amount = False
            if rec.product_line_ids:
                rec.service_warranty_amount = sum(
                    [line.standard_price for line in rec.product_line_ids if line.under_warranty_bool if
                     line.product_id.detailed_type != 'service'])

    ''' this code is used when empty rows in the symptom,defects lines ids then it will raise Validation error on may 09-2025'''

    @api.constrains('defects_type_ids')
    def _check_defect_lines(self):
        if self.env.context.get('skip_state_validation'):
            return False

        for record in self:
            for line in record.defects_type_ids:
                if not line.code:
                    raise ValidationError("Each defect line must have selected.")

    @api.constrains('symptoms_line_ids')
    def _check_symptom_lines(self):
        if self.env.context.get('skip_state_validation'):
            return False

        for rec in self:
            for line in rec.symptoms_line_ids:
                if not line.code:
                    raise ValidationError('Each Symptom line must have selected')

    @api.constrains('service_type_ids')
    def _check_services(self):
        if self.env.context.get('skip_state_validation'):
            return False

        for rec in self:
            for line in rec.service_type_ids:
                if not line.code:
                    raise ValidationError("Service Type must have one service if you Select ")

    def _compute_payment_receipt_count(self):
        for rec in self:
            receipt_count = self.env['payment.receipt'].search_count([('job_card_no_id', '=', rec.id)])
            rec.payment_receipt_count = receipt_count

    def _compute_quotation_count(self):
        for rec in self:
            quotation_count = self.env['service.sale.order'].search_count([('job_task_id', '=', rec.id)])
            rec.quotation_count = quotation_count

    '''  This code is used to Product consume service has allowed only 5 product not more than that by Vijaya bhaskar on may 7 2025'''

    @api.constrains('product_line_ids')
    def _check_change_product_line(self):
        if self.env.context.get('skip_state_validation'):
            return False

        for rec in self:
            if len(rec.product_line_ids) > 5:
                raise ValidationError(
                    "Product Consume Part Service is maximum added only 5 product not more than that(including service product) ")

    # @api.constrains('product_line_ids')
    # def _check_parts_reserved_bool(self):
    #     for rec in self:
    #         if rec.job_card_state_code == '122':
    #             for line in rec.product_line_ids:
    #                 if not line.parts_reserved_bool:
    #                     raise ValidationError("Please enable all products should be parts reserved ")
    #

    def _compute_user_check(self):
        is_user = self.env.user.has_group('machine_repair_management.group_job_card_back_office_user')
        for rec in self:
            rec.check_user = False
            if is_user:
                rec.check_user = True

    '''Commented on Jun - 7 -2025 for replace appointment datetime with planned_date_begin for scheduling'''
    # @api.onchange('service_requested_datetime','planned_date_begin')
    # # @api.onchange('service_requested_datetime','appointment_datetime')
    # def _onchange_call_date(self):
    #     for rec in self:
    #         if rec.service_requested_datetime:
    #             rec.service_request_id.call_request_appointment_date = rec.service_requested_datetime
    #         if rec.planned_date_begin:
    #             rec.service_request_id.technician_appointment_date =rec.planned_date_begin
    #

    '''Commented on Jun - 7 -2025 for replace appointment datetime with planned_date_begin for scheduling'''
    '''Commented by Vijaya Bhaskar on Aug-13-2025 According to client needs if the  technician was free so even if allocated before the scheduled'''

    # @api.constrains('service_requested_datetime','planned_date_begin')
    # # @api.constrains('service_requested_datetime','appointment_datetime')
    # def _service_date_constrains_check(self):
    #     for rec in self:
    #         if rec.service_created_datetime and rec.service_requested_datetime:
    #             ''' service requested  time is atleast 1 hour greater than service created time this modification is done on May 20 2025'''
    #             if rec.service_created_datetime >= rec.service_requested_datetime:
    #                 ''' The service requested date is not equal to created date on May 9 2025'''
    #                 """ if rec.service_created_datetime.strftime("%d-%m-%Y") >= rec.service_requested_datetime.strftime("%d-%m-%Y"):"""
    #                 raise ValidationError('Requested Date and time is always greater than Service Created Date & Time ')
    #
    #         '''Commented on Jun - 7 -2025 for replace appointment datetime with planned_date_begin for scheduling'''
    #         # if rec.service_requested_datetime and rec.appointment_datetime:
    #         #     if rec.service_requested_datetime > rec.appointment_datetime:
    #         #         raise ValidationError("Appointment Date time is always greater than Requested Date & Time")
    #         if rec.service_requested_datetime and rec.planned_date_begin:
    #             if rec.service_requested_datetime > rec.planned_date_begin:
    #                 raise ValidationError("Appt Start Date time is always greater than Requested Date & Time")
    #

    @api.constrains('service_created_datetime', 'planned_date_begin', 'planned_date_end')
    def _service_date_constrains_check(self):
        if self.env.context.get('skip_state_validation'):
            return False

        for rec in self:
            if rec.service_created_datetime and rec.planned_date_begin:
                if rec.service_created_datetime > rec.planned_date_begin:
                    raise ValidationError("Appt Start Date & Time is always greater than Service Created Date & Time")
            if rec.planned_date_begin and rec.planned_date_end:
                if rec.planned_date_begin > rec.planned_date_end:
                    raise ValidationError("Appt End Date & Time is always greater than Appt Start Date & Time")

                    # @api.model

    # def search_fetch(self, domain, field_names, offset=0, limit=None, order=None):
    #     user = self.env.user
    #     # Manager gets all records
    #     # if user.has_group('machine_repair_management.group_machine_repair_manager'):
    #     #     return super(MachineRepairSupport, self).search_fetch(domain, field_names, offset, limit, order)
    #     #
    #     # # Regular user only sees their own records
    #     # if user.has_group('machine_repair_management.group_machine_repair_user'):
    #     #     domain += [('user_id', '=', user.id)]
    #     #     return super(MachineRepairSupport, self).search_fetch(domain, field_names, offset, limit, order)
    #     #
    #
    #     # ##supervisor
    #     if (user.has_group('machine_repair_management.group_job_card_back_office_user') and
    #         user.has_group('machine_repair_management.group_technical_allocation_user')) and user.default_work_center_id:
    #         if user.project_ids.name == 'AMC Project':
    #             domain += [
    #                 ('work_center_id', 'in', user.default_work_center_id.ids), ('amc_project_id', 'in', user.project_ids.ids)
    #             ]
    #             return super(ProjectTask, self).search_fetch(domain, field_names, offset, limit, order)
    #         else:
    #             domain += [
    #                 ('work_center_id', 'in', user.default_work_center_id.ids)
    #             ]
    #             return super(ProjectTask, self).search_fetch(domain, field_names, offset, limit, order)
    #
    #     # ##parts User
    #     if user.has_group('machine_repair_management.group_job_card_back_office_user') and \
    #         user.has_group('machine_repair_management.group_parts_user'):
    #         if user.project_ids.name == 'AMC Project':
    #             domain += [
    #                 ('job_card_state_code', 'in', ('131','129','121', '122')), ('amc_project_id', 'in', user.project_ids.ids)
    #             ]
    #             # domain += [
    #             #     ('job_card_state','=','On Hold - Spare Parts Required'),('job_card_state_code','=','121')
    #             # ]
    #             if user.default_work_center_id:
    #                 domain += [('work_center_id', 'in', user.default_work_center_id.ids)]
    #             return super(ProjectTask, self).search_fetch(domain, field_names, offset, limit, order)
    #         else:
    #             domain += [
    #                 ('job_card_state_code', 'in', ('131', '129', '121', '122'))
    #             ]
    #             # domain += [
    #             #     ('job_card_state','=','On Hold - Spare Parts Required'),('job_card_state_code','=','121')
    #             # ]
    #             if user.default_work_center_id:
    #                 domain += [('work_center_id', 'in', user.default_work_center_id.ids)]
    #             return super(ProjectTask, self).search_fetch(domain, field_names, offset, limit, order)
    #
    #     # For mobile users (technicians)
    #     if user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #         '''Client ask technician also visible closed job card state record on Aug-20-2025'''
    #         # if self.amc_project_id:
    #         print("field_names", field_names)
    #         print("field_names", self)
    #
    #         if user.project_ids.name == 'AMC Project':
    #             domain += [
    #                 ('technician_id', '=', user.id), ('job_card_state_code', 'not in', ('124', '126')), ('amc_project_id', 'in', user.project_ids.ids)
    #             ]
    #             return super(ProjectTask, self).search_fetch(domain, field_names, offset, limit, order)
    #
    #         else:
    #             domain += [
    #                 ('technician_id', '=', user.id), ('job_card_state_code', 'not in', ('124', '126'))
    #             ]
    #             return super(ProjectTask, self).search_fetch(domain, field_names, offset, limit, order)
    #
    #     # if user.has_group('machine_repair_management.group_job_card_back_office_user') and \
    #     #     user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #     #     domain += [
    #     #         ('technician_id', '=', user.id)
    #     #     ]
    #     #     return super(ProjectTask, self).search_fetch(domain, field_names, offset, limit, order)
    #     #
    #
    #     # Default fallback
    #     return super(ProjectTask, self).search_fetch(domain, field_names, offset, limit, order)

    @api.model
    def search_fetch(self, domain, field_names, offset=0, limit=None, order=None):

        user = self.env.user

        # Dynamic AMC Project Check (based on amc_project_id)
        # user.project_ids = list of projects assigned to the user
        amc_project_ids = user.project_ids.ids
        has_amc_project = bool(amc_project_ids)

        # SUPERVISOR FILTER
        if (
                user.has_group('machine_repair_management.group_job_card_back_office_user') and
                user.has_group('machine_repair_management.group_technical_allocation_user') and
                user.default_work_center_id
        ):
            # Always apply work_center filter
            domain += [('work_center_id', 'in', user.default_work_center_id.ids)]

            if has_amc_project:
                domain += [('amc_project_id', 'in', amc_project_ids)]

            return super(ProjectTask, self).search_fetch(domain, field_names, offset, limit, order)

        # PARTS USER FILTER
        if (
                user.has_group('machine_repair_management.group_job_card_back_office_user') and
                user.has_group('machine_repair_management.group_parts_user')
        ):

            # Job card state codes
            domain += [('job_card_state_code', 'in', ('131', '129', '121', '122'))]

            # AMC projects filter
            if has_amc_project:
                domain += [('amc_project_id', 'in', amc_project_ids)]

            # Work center filter if exists
            if user.default_work_center_id:
                domain += [('work_center_id', 'in', user.default_work_center_id.ids)]

            return super(ProjectTask, self).search_fetch(domain, field_names, offset, limit, order)

        # TECHNICIAN (MOBILE USER)
        if user.has_group('machine_repair_management.group_job_card_mobile_user'):

            domain += [
                ('technician_id', '=', user.id),
                ('job_card_state_code', 'not in', ('124', '126'))  # closed states
            ]

            if has_amc_project:
                domain += [('amc_project_id', 'in', amc_project_ids)]

            return super(ProjectTask, self).search_fetch(domain, field_names, offset, limit, order)

        return super(ProjectTask, self).search_fetch(domain, field_names, offset, limit, order)

    # Mobile User only visible

    # product_line_id = fields.Many2one('product.product', string="Product Consume Parts")
    # qty = fields.Float(string="quantity", default=1)
    # price_unit = fields.Float(string='Price')

    @api.constrains('customer_identification_number')
    def _valid_check_customer_validation(self):
        if self.env.context.get('skip_state_validation'):
            return False

        for rec in self:
            if rec.job_card_state_code == '126':
                if rec.customer_identification_scheme:
                    if rec.customer_identification_number:
                        if not rec.customer_identification_number.isdigit():
                            raise ValidationError("Please enter Only Numbers in the identification Numbers")
                        if rec.customer_identification_scheme == 'TIN':
                            if rec.customer_identification_number:
                                if len(rec.customer_identification_number) != 15:
                                    raise ValidationError("Tax identification number is only 15 numbers")
                        elif rec.customer_identification_scheme != 'TIN':
                            if rec.customer_identification_number:
                                if len(rec.customer_identification_number) != 10:
                                    raise ValidationError("Identification number is only 10 numbers")

    @api.onchange('planned_date_begin')
    def _onchange_planned_date_begin(self):
        for rec in self:
            if rec.planned_date_begin:
                rec.planned_date_end = rec.planned_date_begin + timedelta(hours=1)

    def create_inspection_amount(self):
        for rec in self:
            rec.create_receipt()

    def create_receipt(self):
        for rec in self:

            if not rec.team_id:
                raise ValidationError("Please enter Team Leader")



            elif not rec.planned_date_begin:
                raise ValidationError("Please Enter Appt Start Date & Time")

            '''Commented on Jun - 7 -2025 for replace appointment datetime with planned_date_begin for scheduling'''
            # elif not rec.appointment_datetime:
            #     raise ValidationError("Please Enter Appointment Date & Time")
            #

            journal = self.env['account.journal'].search([('type', '=', 'bank')], limit=1)
            payment_method_id = journal.inbound_payment_method_line_ids[
                0].id if journal.inbound_payment_method_line_ids else False
            # payment_amount = float(self.env['ir.config_parameter'].sudo().get_param('machine_repair_management.inspection_amount'))
            payment_amount = self.inspection_charges_amount
            currency = self.env.company.currency_id
            vals = {

                'date': fields.date.today(),
                'job_card_no_id': rec.id,
                'partner_id': rec.partner_id.id or '',
                'customer_name': rec.customer_name or '',
                'amount': payment_amount,
                'journal_id': journal.id,
                'payment_id': payment_method_id,
                'state': 'posted',
                'memo': f'Inspection Charge Amount for {rec.name} - {payment_amount:.2f} {currency.symbol}',

                # 'memo' :f'Inspection Charge Amount for {rec.name}:{payment_amount:.2f}',

            }
            receipt_create = self.env['payment.receipt'].sudo().create(vals)
            rec.payment_receipt_id = receipt_create.id
            if rec.payment_receipt_id:
                journal_entry = self.env['account.move']

                journal_vals = {

                    'move_type': 'entry',
                    # 'account_id': receipt_create.journal_id,
                    # 'amount' :payment_amount,
                    'ref': receipt_create.name,
                    'date': receipt_create.date or False,
                    'journal_id': journal.id,
                }

                debit_account = receipt_create.journal_id.profit_account_id.id
                credit_account = receipt_create.journal_id.loss_account_id.id
                line_vals = []
                debit_vals = {
                    'name': receipt_create.name,
                    'account_id': debit_account,
                    'journal_id': journal.id,
                    'debit': payment_amount,
                    'credit': 0.0,
                    'date': receipt_create.date

                }

                credit_vals = {
                    'name': receipt_create.name,
                    'account_id': credit_account,
                    'journal_id': journal.id,
                    'debit': 0.0,
                    'credit': payment_amount,
                    'date': receipt_create.date

                }

                line_vals.append((0, 0, debit_vals))
                line_vals.append((0, 0, credit_vals))

                transaction = journal_entry.sudo().create(journal_vals)
                transaction.update({'line_ids': line_vals})
                rec.payment_receipt_id.write({'account_move_id': transaction.id})

            # return rec.payment_receipt_id.print_payment_receipt()
            # self.print_inspection_charge_receipt()
            self.inspection_charges_receipt_click = True
            self.send_whatsapp_inspection_receipt()
            self.inspection_charges_receipt_click = False

            return {
                'effect': {
                    'type': 'rainbow_man',
                    'fadeout': 'slow',
                    'message': 'Your Inspection Charges Receipt send Successfully to Customer Whatsapp Number',
                }
            }

            # return

            # return self.print_inspection_charge_receipt()
            # return {
            #         'type': 'ir.actions.report',
            #         'report_name': 'machine_repair_management.report_receipt_payment',
            #         'report_type': 'qweb-pdf'
            #     }
            #

    def show_receipt(self):
        return {
            'name': 'Payment Receipt',
            'res_model': 'payment.receipt',
            "view_mode": 'tree,form',
            "domain": [('job_card_no_id', '=', self.id)],
            "type": 'ir.actions.act_window'

        }

    def create_quotation(self):
        self.ensure_one()

        # Validate prerequisites
        if not self.product_line_ids:
            raise UserError(_('Please add Product details to create a quotation!'))
        elif not self.team_id:
            raise ValidationError("Please enter Team Leader in Job card")

        elif not self.planned_date_begin:
            raise ValidationError("Please Enter Appt Start Date & Time")

        # elif self.product_line_ids:
        #     if self.inspection_charges_bool and self.inspection_charges_amount > 0:
        #         if not any(line.product_id and line.product_id.service_type_bool for line in self.product_line_ids):
        #             raise ValidationError("Please enter service charge amount in the product line")
        #

        # Create sale order
        order_vals = {
            'job_task_id': self.id,
            'customer_name': self.customer_name,
            'customer_address': self.address,
            'service_sale_quotation_date': fields.Datetime.now(),
            # 'partner_id': self.partner_id.id or '',
            # 'user_id': self.partner_id.user_id.id or False,
            'user_id': self.env.uid or '',
            'warehouse_id': self.warehouse_id.id,
            # 'crm_id':False,
            # 'pricelist_id': self.partner_id.property_product_pricelist.id or False,
        }

        order = self.env['service.sale.order'].with_context(from_task=True).create(order_vals)

        # Create order lines
        for line in self.product_line_ids:
            if not line.product_id:
                raise UserError(_('Product not defined on Product Consume/Services!'))

            # Ensure warehouse is set

            self.env['service.sale.order.line'].with_context(from_task=True).create({
                'service_sale_id': order.id,
                'product_id': line.product_id.id,
                'product_qty': line.qty,
                'product_uom': line.uom_id.id,
                'price_unit': 0.0 if line.under_warranty_bool else line.price_unit,
                'vat': line.vat,
                'tax_amount': line.tax_amount,
                'total': line.total,
                # 'name': line.product_id.name or '/',

            })

        # Update task reference
        self.service_sale_id = order.id

        # Update task state if needed
        if order.state == 'draft':
            stage = self.env['project.task.type'].search([('code', '=', '114')], limit=1)
            if stage:
                self.write({
                    'job_state': stage.id,
                    'job_card_state_code': stage.code,
                    'job_card_state': stage.name
                })
                self.service_request_id.service_request_state = stage.name
                self.service_request_id.service_request_state_code = stage.code
                self.service_request_id.state = stage.id

        # Return action
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'service.sale.order',
            'res_id': order.id,
            'views': [(False, 'form')],
            'target': 'current',
            'context': {'create': False},
        }

    def show_quotation(self):
        sale_order = self.env['service.sale.order'].search([('job_task_id', '=', self.id)])
        if sale_order:
            return {
                'name': 'Sale Order',
                'res_model': 'service.sale.order',
                'view_mode': 'tree,form',
                'type': 'ir.actions.act_window',
                'target': 'current',
                "domain": [('job_task_id', '=', self.id)]
            }

    ### It is already worked correctly but time being commeted on Sep 16-2025

    # def show_quotation(self):
    #     sale_order = self.env['service.sale.order'].search([('job_task_id', '=', self.id)])
    #     return {
    #         'name': 'Sale Order',
    #         'res_model': 'service.sale.order',
    #         # 'res_id': sale_order.ids, #this is for single record showing
    #         'view_mode': 'tree,form',
    #         'type': 'ir.actions.act_window',
    #         'target': 'current',
    #         "domain":[('job_task_id', '=', self.id)]
    #     }
    #     return {}

    '''Code is added on August-29-2025 by Vijaya Bhaskar due to Technician ask to add extra job card work for a single customer'''

    def duplicate_service_job_card_create(self):
        self.ensure_one()
        duplicate_service_record = self.service_request_id.with_context(
            skip_state_validation=True
        ).copy_data()[0]

        service_request_creation = self.env['machine.repair.support'].with_context(
            skip_state_validation=True
        ).create(duplicate_service_record)

        # duplicate_service_record = self.service_request_id.copy_data()[0]
        # service_request_creation = self.env['machine.repair.support'].create(duplicate_service_record)
        service_request_creation.write({'symptom_line_ids': [(0, 0, {'sym_id': line.sym_id.id}) for line in
                                                             self.service_request_id.symptom_line_ids],
                                        'problem': self.service_request_id.problem}
                                       )

        service_request_creation.task_id.write({'symptoms_line_ids': [(0, 0, {'code': line.sym_id.id}) for line in
                                                                      self.service_request_id.symptom_line_ids]})
        service_request_creation.task_id.team_id = self.team_id.id
        service_request_creation.task_id.technician_id = self.technician_id.id
        service_request_creation.task_id._onchange_team_id_warehouse()
        service_request_creation.task_id.planned_date_begin = fields.Datetime.now() + timedelta(hours=1)
        service_request_creation.task_id._onchange_planned_date_begin()
        service_request_creation.task_id.product_line_ids = [(5, 0, 0)]
        service_request_creation.task_id.product_id = None
        service_request_creation.attachment_ids = [(5, 0, 0)]
        service_request_creation.task_id.attachment_ids = [(5, 0, 0)]
        service_request_creation.sr_service_warranty_id = None
        service_request_creation.purchase_invoice_no = None
        service_request_creation.purchase_date = None
        service_request_creation.dealer_id = None
        service_request_creation.product_sub_group_id = None
        service_request_creation.product_id = None
        service_request_creation.product_slno = None

        service_request_creation.task_id.service_warranty_id = None
        service_request_creation.task_id.purchase_invoice_no = None
        service_request_creation.task_id.purchase_date = None
        service_request_creation.task_id.dealer_id = None
        service_request_creation.task_id.product_sub_group_id = None
        service_request_creation.task_id.product_id = None
        service_request_creation.task_id.product_slno = None

        service_request_creation.task_id.technician_first_visit_id = self.technician_id.id
        service_request_creation.task_id.technician_first_visit = self.technician_id.name
        service_request_creation.task_id.technician_first_visit_date = fields.Date.today()

        service_request_creation.task_id.img1_text = "Unit Name Plate"
        service_request_creation.task_id.img2_text = "Unit Part"

        # stage = self.env['project.task.type'].search([('code', '=', '111')], limit=1)
        stage = self.env['project.task.type'].search([('code', '=', '110')], limit=1)

        if stage:
            service_request_creation.task_id.with_context(skip_state_validation=True).sudo().write({
                'job_state': stage.id,
                'job_card_state_code': stage.code,
                'job_card_state': stage.name
            })
            service_request_creation.service_request_state = stage.name
            service_request_creation.service_request_state_code = stage.code
            service_request_creation.state = stage.id

        self.duplicate_service_button_clicked = True

        ## this is also Worked
        # message = "Additional Job Card Created Successfully: %s" % service_request_creation.task_id.name
        #
        # # Return both the notification and the action to open the form
        # return {
        #     'type': 'ir.actions.client',
        #     'tag': 'display_notification',
        #     'params': {
        #         'title': 'Success',
        #         'message': message,
        #         'type': 'success',
        #         'sticky': False,
        #         'next': {
        #             'type': 'ir.actions.act_window',
        #             'name': 'Job Card',
        #             'res_model': 'project.task',
        #             'view_mode': 'form',
        #             'res_id': service_request_creation.task_id.id,
        #             'views': [[False, 'form']],
        #             'target': 'current',
        #         },
        #     }
        # }
        #

        action = {

            'type': 'ir.actions.act_window',
            'name': 'Job Card',
            'res_model': 'project.task',
            'view_mode': 'form',
            'res_id': service_request_creation.task_id.id,
            'views': [(False, 'form')],
            'target': 'current',
        }

        return {

            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'success',
                'message': 'Additional Job Card Created Successfully %s' % service_request_creation.task_id.name,
                'type': 'success',
                'sticky': False,
                'next': action

            }

        }

    def cancelled_reason_button(self):
        return {

            'type': 'ir.actions.act_window',
            'res_model': 'cancelled.reason.wizard',
            'name': 'Cancelled Reason',
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'new',
            'context': {
                'default_job_card_id': self.id,
            },

        }

    # @api.onchange('job_state')
    # def _onchange_job_state_show_cancel_wizard(self):
    #     if self.job_state:
    #         print("........................selffffffffffffffffffffffff",self.job_state.code,self.job_state)
    #         # state = self.env['project.task.type'].browse(self.job_state)
    #         if self.job_state.code == '124':
    #             # print(".............onchnageeeeee")
    #             #
    #             # # action = self.cancelled_reason_button_mobile()
    #             # # self.job_state = False
    #             # # return action
    #             #
    #             # return self.cancelled_reason_button_mobile()
    #             # print("......................job_state",self.job_state.code)
    #             #
    #             return {
    #             # 'warning': {
    #             #     'title': "Action Required",
    #             #     'message': "You selected the Cancelled job state. Please provide a reason!",
    #             # },
    #             'type': 'ir.actions.act_window',
    #             'res_model': 'cancelled.reason.wizard',
    #             'name': 'Cancelled Reason',
    #             'view_mode': 'form',
    #             'views': [(False, 'form')],
    #             'target': 'new',
    #             'context': {'default_job_card_id': self.id},
    #         }

    def cancelled_reason_button_mobile(self):

        if self.job_state.code == '124':
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'cancelled.reason.wizard',
                'name': 'Cancelled Reason',
                'view_mode': 'form',
                'target': 'new',
                # 'domain': [('id', 'in', job_card_search.ids)],
                'views': [
                    (self.env.ref('machine_repair_management.cancelled_reason_wizard_form_view').id, 'form'),
                    (False, 'form')
                ],
                'context': {'default_job_card_id': self.id, }
            }

    def action_check_wizard(self):
        pass
        # self.cancel_button_wizard_bool = True
        # action = {
        #     'type': 'ir.actions.act_window',
        #     'res_model': 'cancelled.reason.wizard',
        #     'name': 'Cancelled Reason',
        #     'view_mode': 'form',
        #     'view_type': 'form',
        #     'views': [(self.env.ref('machine_repair_management.cancelled_reason_wizard_form_view').id, 'form')],
        #     'target': 'new',
        #     'context': {
        #         'default_job_card_id': self.id,
        #     },
        # }
        # print("..............................DEBUG: Wizard action =", action)
        # return action

    # def create_quotation(self):
    #     self.ensure_one()
    #     for rec in self:
    #         if not rec.send roduct_line_ids:
    #             raise UserError(_('Please add Product detail to create a quotation!'))
    #
    #         elif not rec.team_id:
    #             raise ValidationError("Please enter Team Leader in Job card")
    #
    #         elif not rec.technician_id:
    #             raise ValidationError("Please Enter Technician Name ")
    #
    #         elif not rec.service_requested_datetime:
    #             raise ValidationError("Please Enter  Requested Date & Time")
    #
    #         elif not rec.planned_date_begin:
    #             raise ValidationError("Please Enter Appt Start Date & Time")
    #
    #         '''Commented on Jun - 7 -2025 for replace appointment datetime with planned_date_begin for scheduling'''
    #         # elif not rec.appointment_datetime:
    #         #     raise ValidationError("Please Enter Appointment Date & Time")
    #         #
    #
    #         values = {
    #             'task_id': rec.id,
    #             'partner_id': rec.partner_id.id,
    #             'user_id': rec.partner_id.user_id.id or False,
    #              'pricelist_id': rec.partner_id.property_product_pricelist.id if rec.partner_id.property_product_pricelist else False,
    #         }
    #         # order_id = self.env['sale.order'].sudo().create(values)
    #
    #         order_id = self.env['sale.order'].with_context(from_task=True).sudo().create(values)
    #
    #         for line in rec.product_line_ids:
    #             if not line.product_id:
    #                 raise UserError(_('Product not defined on Product Consume/Services!'))
    #
    #             orderlinevals = {
    #                 'order_id': order_id.id,
    #                 'product_id': line.product_id.id,
    #                 'product_uom_qty': line.qty,
    #                 'product_uom': line.uom_id.id,
    #                 'price_unit': line.price_unit if not line.under_warranty_bool else 0.0,  # Directly using the provided line price
    #                 # 'name': '',  # 14/02/2020
    #                 'name': line.product_id.name or '/',  # 14/02/2020
    #
    #             }
    #             # self.env['sale.order.line'].create(orderlinevals)
    #             self.env['sale.order.line'].with_context(from_task=True).create(orderlinevals)
    #         rec.sale_id = order_id.id
    #
    #         if rec.sale_id:
    #             if rec.sale_id.task_id:
    #                 if rec.sale_id.state == 'draft':
    #                     stage_search = self.env['project.task.type'].search(
    #                         [('code','=','114')],limit = 1)
    #                     if stage_search:
    #                         rec.job_state = stage_search
    #                         rec.job_card_state_code = stage_search.code
    #                         rec.job_card_state = stage_search.name
    #                         self._onchange_job_card_state_status()
    #
    #
    #         action = self.env.ref('sale.action_quotations').sudo().read()[0]
    #         action['domain'] = [('id', '=', order_id.id)]
    #         action['views'] = [
    #             (self.env.ref('sale.view_order_tree').id, 'tree'),
    #             (self.env.ref('sale.view_order_form').id, 'form'),
    #         ]
    #         action['context'] = {'create': False if order_id.task_id else True}
    #         return action

    # action = self.env.ref('sale.action_quotations')
    # result = action.sudo().read()[0]
    # result['domain'] = [('id', '=', order_id.id)]
    # return result

    # def action_add_product_line(self):
    #     return {
    #         'type': 'ir.actions.act_window',
    #         'name': 'Add Product Line',
    #         'view_mode': 'form',
    #         'res_model': 'product.lines',
    #         'target': 'new',
    #         'context': {
    #             'default_project_task_id': self.id,
    #         }
    #     }

    def action_add_product_line(self):
        '''This is for Used for check product_line_ids more than 5 products in mobile version'''
        for rec in self:
            if rec.product_line_ids:
                if len(rec.product_line_ids) > 4:
                    raise ValidationError(
                        "Product Consume Part Service is maximum added only 5 product not more than that(including service product) ")
        return {
            'type': 'ir.actions.act_window',
            'name': 'Add Product Line',
            'view_mode': 'form',
            'res_model': 'product.lines',
            'target': 'new',
            'view_id': self.env.ref('machine_repair_management.view_product_lines_form').id,
            'context': {
                'default_project_task_id': self.id, 'default_amc_project_bool': bool(self.project_related_amc_bool),
            }
        }

    # ## for time being commeted by Vijaya Bhaskar on August 18 2025 because closed date time has error based on the planned date begin
    # @api.constrains('planned_date_begin', 'planned_date_end')
    # def _check_planned_date_time_check(self):
    #     for rec in self:
    #         if rec.planned_date_begin and rec.planned_date_end:
    #             user_tz = self.env.user.tz or 'UTC'
    #             # Convert server time "now" to user's timezone
    #             now_user_tz = fields.Datetime.context_timestamp(rec, fields.Datetime.now())
    #             # Convert planned datetimes to user's timezone
    #             planned_begin_user_tz = fields.Datetime.context_timestamp(rec, rec.planned_date_begin)
    #             planned_end_user_tz = fields.Datetime.context_timestamp(rec, rec.planned_date_end)
    #             print("now_user_tz, planned_begin_user_tz, planned_end_user_tz", now_user_tz, planned_begin_user_tz, planned_end_user_tz)
    #
    #             if planned_begin_user_tz < now_user_tz or planned_end_user_tz < now_user_tz:
    #                 raise ValidationError(
    #                     "Both Appt Start Date & Time and Appt End Date & Time must be in the future "
    #                     f"(based on your local time: {user_tz})."
    #                 )
    #

    @api.constrains('technician_id', 'planned_date_begin', 'planned_date_end')
    def _check_technician_app_time_check(self):
        if self.env.context.get('skip_state_validation'):
            return False
        for rec in self:

            if rec.technician_id and rec.planned_date_begin and rec.planned_date_end:

                overlapping_tasks = self.search([
                    ('id', '!=', rec.id),
                    ('technician_id', '=', rec.technician_id.id),
                    ('planned_date_begin', '<', rec.planned_date_end),
                    ('planned_date_end', '>', rec.planned_date_begin),
                    ('job_card_state_code', 'not in', ('126', '124'))
                ])

                if overlapping_tasks:
                    overlapping_names = ', '.join(overlapping_tasks.mapped('name'))
                    raise ValidationError(
                        f"The technician '{rec.technician_id.name}' is already allocated "
                        f"to another task during this time: '{overlapping_names}'."
                    )

    @api.depends('service_created_datetime')
    def _compute_job_request_date_time(self):
        for record in self:
            record.call_date = False
            record.call_time = False
            if record.service_created_datetime:
                user_tz = self.env.user.tz or 'UTC'
                user_timezone = pytz.timezone(user_tz)
                local_dt = pytz.utc.localize(record.service_created_datetime).astimezone(user_timezone)

                record.call_date = local_dt.date()
                record.call_time = local_dt.strftime('%H:%M:%S')
                # record.call_date = record.service_created_datetime.date()
                # record.call_time = record.service_created_datetime.strftime('%H:%M:%S')

    @api.depends('closed_datetime')
    def _compute_job_close_datetime(self):
        for record in self:
            record.closed_date = False
            record.closed_time = False
            if record.closed_datetime:
                user_tz = self.env.user.tz or 'UTC'
                user_timezone = pytz.timezone(user_tz)
                local_tz = pytz.utc.localize(record.closed_datetime).astimezone(user_timezone)

                record.closed_date = local_tz.date()
                record.closed_time = local_tz.strftime('%H:%M:%S')

                # record.closed_date = record.closed_datetime.date()
                # record.closed_time = record.closed_datetime.strftime('%H:%M:%S')

    '''Commented on Jun - 7 -2025 for replace appointment datetime with planned_date_begin for scheduling'''

    # @api.depends('appointment_datetime')
    @api.depends('planned_date_begin')
    def _compute_job_appointment_datetime(self):
        for record in self:
            record.appt_date = False
            record.appt_time = False
            if record.planned_date_begin:
                if record.planned_date_begin:
                    user_tz = self.env.user.tz or 'UTC'
                    user_timezone = pytz.timezone(user_tz)
                    local_timezone = pytz.utc.localize(record.planned_date_begin).astimezone(user_timezone)

                    record.appt_date = local_timezone.date()
                    record.appt_time = local_timezone.strftime('%H:%M:%S')
                    # record.appt_date = record.appointment_datetime.date()
                    # record.appt_time = record.appointment_datetime.strftime('%H:%M:%S')

    @api.depends('service_requested_datetime')
    def _compute_service_requested_date(self):
        for rec in self:
            rec.service_request_date = False
            rec.service_request_time = False
            if rec.service_requested_datetime:
                user_tz = self.env.user.tz or 'UTC'
                user_timezone = pytz.timezone(user_tz)
                local_timezone = pytz.utc.localize(rec.service_requested_datetime).astimezone(user_timezone)
                rec.service_request_date = local_timezone.date()
                rec.service_request_time = local_timezone.strftime("%H:%M:%S")

    @api.onchange('product_id')
    def _brand_models_onchange(self):
        for rec in self:
            if rec.product_id:
                rec.brand = rec.product_id.brand
                rec.model = rec.product_id.model
                # rec.product_slno = rec.product_id.default_code
                # rec.product_slno = rec.product_id.model

    ###### currently working
    # @api.depends('service_created_datetime', 'closed_datetime')
    # def _compute_rtat_hours(self):
    #     for record in self:
    #         if record.service_created_datetime and record.closed_datetime:
    #             delta = record.closed_datetime - record.service_created_datetime
    #             record.rtat_hours = delta.total_seconds() / 3600
    #         else:
    #             record.rtat_hours = 0.0

    ''' THIS IS WORKS GOOD commented by Vijaya Bhaskar on May 17 2025'''
    # @api.depends('service_created_datetime', 'closed_datetime')
    # def _compute_rtat_hours(self):
    #     for record in self:
    #         if record.service_created_datetime and record.closed_datetime:
    #             start = fields.Datetime.to_datetime(record.service_created_datetime)
    #             end = fields.Datetime.to_datetime(record.closed_datetime)
    #
    #             # Get company's calendar (to check non-working days)
    #             calendar = record.env.company.resource_calendar_id
    #
    #             if not calendar:
    #                 # Fallback: Exclude weekends (Sat & Sun) only
    #                 delta = end - start
    #                 total_hours = delta.total_seconds() / 3600.0
    #
    #                 # Count weekend days (Sat & Sun)
    #                 weekend_days = 0
    #                 current_day = start.date()
    #                 end_day = end.date()
    #
    #                 while current_day <= end_day:
    #                     if current_day.weekday() in (5, 6):  # Saturday (5) or Sunday (6)
    #                         weekend_days += 1
    #                     current_day += timedelta(days=1)
    #
    #                 # Subtract 24 hours per weekend day
    #                 total_hours -= weekend_days * 24
    #                 record.rtat_hours = max(total_hours, 0.0)
    #             else:
    #                 # Use company calendar to exclude non-working days (full days)
    #                 total_hours = (end - start).total_seconds() / 3600.0
    #                 non_working_days = 0
    #
    #                 current_day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    #                 end_day = end.replace(hour=0, minute=0, second=0, microsecond=0)
    #
    #                 while current_day <= end_day:
    #                     next_day = current_day + timedelta(days=1)
    #
    #                     # Check if the day is a non-working day (weekends + holidays)
    #                     work_hours = calendar.get_work_hours_count(
    #                         current_day,
    #                         next_day,
    #                         compute_leaves=True
    #                     )
    #                     if work_hours <= 0:  # If no working hours, it's a non-working day
    #                         non_working_days += 1
    #
    #                     current_day = next_day
    #
    #                 # Subtract 24 hours per non-working day
    #                 total_hours -= non_working_days * 24
    #                 record.rtat_hours = max(total_hours, 0.0)
    #         else:
    #             record.rtat_hours = 0.0

    ''' This code is worked based on the default company user has resource calendar and that will exclude the weekend days'''
    '''technician_started_date,technician_reached_date '''

    @api.depends('service_created_datetime', 'closed_datetime', 'job_card_state_code', 'job_resume_date',
                 'job_hold_date')
    def _compute_rtat_hours(self):
        for record in self:
            # if record.job_card_state_code =='124':
            #     record.rtat_hours = 0.0
            #     continue

            record.rtat_hours = 0.0  # Default value
            if record.service_created_datetime and record.closed_datetime:
                start = fields.Datetime.to_datetime(record.service_created_datetime)
                end = fields.Datetime.to_datetime(record.closed_datetime)

                calendar = record.env.company.resource_calendar_id
                delta = end - start
                total_hours = delta.total_seconds() / 3600.0

                if not calendar:
                    weekend_days = sum(1 for day in (start.date() + timedelta(days=i)
                                                     for i in range((end.date() - start.date()).days + 1) if
                                                     day.weekday() in (5, 6)))
                    total_hours -= weekend_days * 24
                else:
                    non_working_days = 0
                    current_day = start.replace(hour=0, minute=0, second=0, microsecond=0)
                    end_day = end.replace(hour=0, minute=0, second=0, microsecond=0)

                    while current_day <= end_day:
                        next_day = current_day + timedelta(days=1)
                        if calendar.get_work_hours_count(current_day, next_day, compute_leaves=True) <= 0:
                            non_working_days += 1
                        current_day = next_day
                    total_hours -= non_working_days * 24

                record.rtat_hours = max(total_hours, 0.0)

                if record.job_resume_date and record.job_hold_date:
                    job_resume_date = fields.Datetime.to_datetime(record.job_resume_date)
                    job_hold_date = fields.Datetime.to_datetime(record.job_hold_date)
                    on_hold_hours = job_resume_date - job_hold_date
                    total_onhold_worked_hours = (on_hold_hours.total_seconds()) / 3600

                    record.rtat_hours = record.rtat_hours - (total_onhold_worked_hours)

                if record.job_card_state_code == '124':
                    record.rtat_hours = 0.0

    @api.constrains('planned_date_begin', 'planned_date_end')
    def _valid_check_planned_date_begin_date_end(self):

        if self.env.context.get('skip_state_validation'):
            return False

        for rec in self:

            if not rec.planned_date_begin or not rec.planned_date_end:
                continue

            calendar = rec.env.company.resource_calendar_id

            working_day = set(int(att.dayofweek) for att in calendar.attendance_ids)

            for field_name in ['planned_date_begin', 'planned_date_end']:
                field_date = getattr(rec, field_name)
                work_day = field_date.weekday()
                if work_day not in working_day:
                    raise ValidationError("Date is not comes under Company Working Day")
            leaves_search = self.env['resource.calendar.leaves'].search(
                [
                    ('calendar_id', '=', calendar.id),
                    ('date_from', '<=', rec.planned_date_end),
                    ('date_to', '>=', rec.planned_date_begin)
                ])
            for leave in leaves_search:
                if leave.date_from.date() <= rec.planned_date_begin.date() <= leave.date_to.date() or \
                        leave.date_from.date() <= rec.planned_date_end.date() <= leave.date_to.date():
                    raise ValidationError("Planned dates are not comes under public holiday")

            # if record.rtat_hours !=0.0:
            #     '''Update time sheet'''
            #     val_lst = [(5,0,0)]
            #     vals = {
            #         'date' : self.service_created_datetime.date(),
            #         'user_id' : self.technician_id.id,
            #         'project_id':self.project_id.id,
            #         'company_id':self.company_id.id,
            #         'name': self.name,
            #         'unit_amount':record.rtat_hours,
            #         }
            #
            #     val_lst.append((0,0,vals))
            #
            #     record.timesheet_line_ids = val_lst
            # else:
            #     val_lst = [(5,0,0)]
            #     record.timesheet_line_ids = val_lst

    ''' currently working commented by Vijaya bhaskar on Jul 17 2025 they don't want separate inspection charges amount invoice 

    @api.depends('product_line_ids')
    def _compute_grand_total(self):
        for order in self:
            order.grand_total = sum(line.total for line in order.product_line_ids)
    '''

    @api.depends('product_line_ids', 'inspection_charges_amount', 'inspection_charges_bool',
                 'final_inspection_charges_amount', 'balance_amount_received_bool', 'service_grand_total_amount')
    def _compute_grand_total(self):
        for order in self:
            order.grand_total = sum(line.total for line in order.product_line_ids)
            order.balance_paid = abs(order.grand_total - order.final_inspection_charges_amount)
            if order.inspection_charges_bool and not order.balance_amount_received_bool:
                if order.final_inspection_charges_amount > 0 and (
                        order.grand_total == 0 or order.grand_total < order.final_inspection_charges_amount):
                    order.balance_paid = 0.0
            if order.balance_amount_received_bool and order.inspection_charges_bool:
                if order.final_inspection_charges_amount > 0:
                    order.balance_paid = abs(
                        order.grand_total - (order.balance_paid + order.final_inspection_charges_amount))
                else:
                    order.balance_paid = abs(order.grand_total - order.balance_paid)

            # if order.inspection_charges_bool  and order.inspection_charges_amount:
            #     if order.grand_total > 0:
            #         if order.inspection_charges_amount > 0 and order.final_inspection_charges_amount > 0:
            #             order.grand_total  = order.grand_total - order.final_inspection_charges_amount
            #             if not order.balance_amount_received_bool:
            #                 if order.final_inspection_charges_amount == order.service_grand_total_amount:
            #                     order.balance_paid = 0
            #                 else:
            #                     order.balance_paid  = order.grand_total - order.final_inspection_charges_amount
            #                     if order.balance_paid < 0 :
            #                         order.balance_paid = 0.0
            #             elif order.balance_amount_received_bool and order.inspection_charges_bool :
            #                 order.balance_paid  = 0.0

    ''' currently working commented by Vijaya bhaskar on Jul 10 2025 due to customer name is not taken from the res.partner
    @api.depends('partner_id')
    def _compute_address(self):
        for rec in self:
            rec.longitude = False
            rec.latitude = False
            if rec.partner_id:
                address_parts = [
                        rec.partner_id.street or  False,
                        rec.partner_id.street2 or False,
                        rec.partner_id.customer_city_id.name if rec.partner_id.customer_city_id else False,
                        rec.partner_id.state_id.name if rec.partner_id.state_id else False,
                        rec.partner_id.country_id.name if rec.partner_id.state_id else False,
                        rec.partner_id.zip or False
                    ]
                rec.address = ",".join(filter(None, address_parts))
                rec.longitude = rec.partner_id.partner_longitude
                rec.latitude = rec.partner_id.partner_latitude
            else:
                rec.address = False
                rec.longitude = False
                rec.latitude = False

    '''

    # rec.address = rec.partner_id.contact_address if rec.partner_id else ''

    # @api.constrains('appointment_datetime')
    # def _check_appointment_datetime(self):
    #     for rec in self:
    #         if rec.appointment_datetime:
    #             if rec.appointment_datetime < fields.Datetime.now():
    #                 raise ValidationError("Appointment Date & Time must be in the future.")

    # @api.constrains('closed_datetime')
    # def _check_closed_datetime(self):
    #     for rec in self:
    #         '''Commented on Jun - 7 -2025 for replace appointment date time with planned_date_begin for scheduling'''
    #
    #         # if rec.appointment_datetime and rec.closed_datetime:
    #         #     if rec.appointment_datetime > rec.closed_datetime:
    #         #         raise ValidationError('Closed Date & Time is always greater than Appointment Date & Time')
    #         if rec.planned_date_begin and rec.closed_datetime:
    #             if rec.planned_date_begin > rec.closed_datetime:
    #                 raise ValidationError('Closed Date & Time is always greater than Appt Start Date & Time')
    #             # if rec.closed_datetime < fields.Datetime.now():
    #             #     raise ValidationError("Closed Date & Time must be in the future.")
    #             if rec.closed_datetime:
    #                 if rec.closed_datetime.date() > fields.Date.today():
    #                     raise ValidationError("Closed Date & Time is not greater than today date")

    @api.onchange('warranty')
    def _compute_warranty_expiry(self):
        for rec in self:
            rec.warranty_expiry_date = False
            if rec.warranty and rec.purchase_date:
                if rec.product_category_id.warranty_period_combo == 'days':
                    rec.warranty_expiry_date = rec.purchase_date + timedelta(
                        days=rec.product_category_id.warranty_period)
                elif rec.product_category_id.warranty_period_combo == 'months':
                    rec.warranty_expiry_date = rec.purchase_date + relativedelta(
                        months=rec.product_category_id.warranty_period)
                elif rec.product_category_id.warranty_period_combo == 'years':
                    rec.warranty_expiry_date = rec.purchase_date + relativedelta(
                        years=rec.product_category_id.warranty_period)
                else:
                    rec.warranty_expiry_date = False

    # @api.constrains('product_line_ids', 'job_card_state_code')
    # def _check_parts_ready(self):
    #     for rec in self:
    #         if rec.job_card_state_code in ('122', '126'):
    #             if rec.product_line_ids:
    #                 # for line in rec.product_line_ids:
    #                 #     if not line.parts_reserved_bool:
    #                 #         raise ValidationError("Product %s  is not reserved with Product Consume Parts/Services")
    #                 #
    #                 for line in rec.product_line_ids:
    #                     if line.product_id:
    #                         if not line.parts_reserved_bool:
    #                             raise ValidationError("Please check all the Products should be Reserved.This Product is not reserved")
    #                     if line.on_hand_qty == 0.0:
    #                         raise ValidationError("Please Stock is not available %s.Please Contact Administrator" % line.product_id.display_name)
    #
    #             if not rec.product_line_ids:
    #                 raise ValidationError("Please give any one of the Product in the product consume Part/services")
    #
    #         # if rec.job_card_state_code == '126':
    #         #     if rec.product_line_ids:
    #         #         if self.inspection_charges_bool and self.inspection_charges_amount > 0:
    #         #             if not any(line.product_id and line.product_id.service_type_bool for line in rec.product_line_ids):
    #         #                 raise ValidationError("Please enter service charge amount in the product line")
    #         #

    # # currently working but commented by Vijaya bhaskar on Jun 02-2025 due to not required for the invoice genrated automatically not based on the location

    '''@api.model
    def create(self, vals):
        # Generate sequence if job_card_state_code is '126' on creation

        if vals.get('job_card_state_code') == '126':
            vals['invoice_no'] = self._generate_jobcard_sequence(vals)
        return super(ProjectTask, self).create(vals)

    def write(self, vals):
        # Generate sequence if job_card_state_code is updated to '126'
        for task in self:
            if vals.get('job_card_state_code') == '126' and not task.invoice_no:
                vals['invoice_no'] = self._generate_jobcard_sequence(vals)
        return super(ProjectTask, self).write(vals) 


    def _generate_jobcard_sequence(self, vals):
        now = datetime.now()
        current_month = now.month
        current_year = now.year
        year_str = now.strftime("%y")
        month_str = now.strftime("%m")

        sequence = self.env['ir.sequence'].search([('code', '=', 'jobcard.sequence')], limit=1)
        if not sequence:
            raise ValidationError("Sequence 'jobcard.sequence' not found!")

        loc = "JC -"  
        number = 1     
        location_id = self.work_center_id


        if sequence.use_date_range and sequence.use_location_wise:
            domain = [
                ('sequence_id', '=', sequence.id),
                ('date_from', '<=', now.date()),
                ('date_to', '>=', now.date()),
                ('work_center_id', '=', location_id.id)
            ]
            date_range = self.env['ir.sequence.date_range'].search(domain, limit=1)
            if date_range:
                loc = date_range.location_code or loc
                number = date_range.number_next_actual
                date_range.write({'number_next_actual': number + 1})


        elif sequence.use_date_range:
            domain = [
                ('sequence_id', '=', sequence.id),
                ('date_from', '<=', now.date()),
                ('date_to', '>=', now.date())
            ]
            date_range = self.env['ir.sequence.date_range'].search(domain, limit=1)
            if date_range:
                loc = date_range.location_code or loc
                number = date_range.number_next_actual
                date_range.write({'number_next_actual': number + 1})


        else:
            number = sequence.number_next_actual
            sequence.write({'number_next_actual': number + 1})


        seq = f"{loc}{year_str}{month_str}{str(number).zfill(4)}"


        duplicate = self.env['project.task'].search([('invoice_no', '=', seq)], limit=1)
        if duplicate:
            raise ValidationError(f"Job Card with invoice number '{seq}' already exists!")

        return seq  '''

    # @api.model
    # def write(self, vals):
    #     res = super(ProjectTask, self).write(vals)
    #     if 'code' in vals:
    #         for rec in self:
    #             if rec.project_task_id and rec.project_task_id.service_request_id:
    #                 repair_supports = self.env['machine.repair.support'].search([
    #                     ('service_request_id', '=', rec.project_task_id.service_request_id.id)
    #                 ])
    #                 for repair in repair_supports:
    #                     repair_lines = [(5, 0, 0)]
    #                     for symptom in rec.code:
    #                         repair_vals = {'sym_id': symptom.id}
    #                         repair_lines.append((0, 0, repair_vals))
    #                     repair.symptom_line_ids = repair_lines
    #     return res

    '''Schedule for Invoice send whatsapp added on Jun 17 2025 by Vijaya Bhaskar'''

    @api.model
    def _send_jobcard_whatsapp_invoice(self):

        job_card_search = self.env['project.task'].search([
            ('job_card_state_code', '=', '126'),
            ('job_card_state', 'ilike', 'closed'),
            ('invoice_no', '!=', False),
            ('whatsapp_invoice_sent', '=', False)])

        for job in job_card_search:
            if job.invoice_no and not job.whatsapp_invoice_sent:
                try:
                    job.send_scheduler_whatsapp_invoice_receipt()
                    job.sudo().write({'whatsapp_invoice_sent': True})
                    _logger.info("Successfully sent WhatsApp invoice for job card %s", job.name)
                except Exception as e:
                    _logger.error("Failed to send WhatsApp invoice for job card %s: %s", job.name, str(e))

    # '''This is for schduler invoice send automatically on June-25-2025 added by Vijaya bhaskar'''
    def send_scheduler_whatsapp_invoice_receipt(self):
        if not self.whatsapp_send_bool:
            _logger.info("❌ No WhatsApp set in res Config Settings")
            return False
        self.ensure_one()
        phone_number = self.phone
        country_code = self.country_id.phone_code

        if not phone_number:
            _logger.info("❌ No Phone Number is linked")
            return
        phone_number = phone_number.replace('+', '').replace(' ', '')
        phone_number = f"{country_code}{phone_number}"

        whatsapp_opt_in = self.whatsapp_opt_in
        if not whatsapp_opt_in:
            _logger.info("❌ No WhatsApp opt-in for Customer %s", self.customer_name)
            return False

        pdf_content = False
        try:
            report = self.env['ir.actions.report'].sudo()
            datas = self.print_job_card_invoice().get('data', {})
            pdf_content, _ = report._render_qweb_pdf(
                'machine_repair_management.print_job_card_invoice_template_document',
                res_ids=[self.id],
                data=datas
            )
            _logger.info("PDF generated successfully for job card %s", self.name)

            # pdf_content, _ = self.env['ir.actions.report'].sudo()._render_qweb_pdf(
            #     'machine_repair_management.print_job_card_invoice_template_document',
            #     [self.id],
            #     data=datas
            # )

        except Exception as e:
            _logger.error("Error rendering PDF for job card %s: %s", self.name, str(e))
            raise ValidationError(f"Failed to generate PDF: {str(e)}")

        file_name = f"Invoice {self.invoice_no}.pdf"
        media_id = self._upload_pdf_meta(pdf_content, file_name)
        if not media_id:
            _logger.info("❌ Failed to upload the media id %s", self.name)
            return

        self.send_pdf_to_whatsapp(phone_number, media_id, file_name, self.name)
        self.whatsapp_invoice_sent = True
        return {
            'effect': {
                'type': 'rainbow_man',
                'fadeout': 'slow',
                'message': 'Your Invoice send Successfully to Customer Whatsapp Number',
            }
        }

    def print_job_card(self):
        job_lst = []
        product_lines = []
        total_amt_lst = []
        total_extended_price = 0.00
        total_vat_amt = 0.00
        extended_price = 0.00
        grand_total = 0.00
        total = 0.00
        amount_words = False
        quote = self.env['service.sale.order'].search([('job_task_id.name', '=', self.name), ('state', '!=', 'cancel')])
        for job in self:
            vals = {
                'warehouse_id': job.warehouse_id.name,
                'cic_ref_no': job.control_card_no,
                'partner_id': job.partner_id.name,
                'customer_name': job.customer_name or '',
                'address': job.address,
                'vat': job.partner_id.vat,
                'job_card_no': job.name,
                'remarks': job.supervisor_comments,
                'quotation_no': quote.name,
                'quotation_date': quote.service_sale_quotation_date,
                'quotation_expiry_date': quote.service_sale_quotation_date,
                'technician_name': job.technician_id.name,
                'company_vat': self.env.company.vat,
                'model_no': job.product_id.default_code or None,
                'serial_no': job.product_slno or None,

            }
            job_lst.append(vals)
        for product in self.product_line_ids:
            extended_price = product.price_unit
            total = product.total
            # total = extended_price + product.tax_amount

            product_vals = {
                'stock_group': product.product_id.categ_id.name,
                'stock_number': product.product_id.default_code,
                'description': product.product_id.name,
                'qty': product.qty,
                'unit_price': product.price_unit,
                'unit_discount': '',
                'net_unit_price': product.price_unit,
                'extended_price': extended_price,
                'vat_percent': product.vat if not product.under_warranty_bool else 0.00,
                'vat_amount': product.tax_amount if not product.under_warranty_bool else 0.00,
                'total': product.total if not product.under_warranty_bool else 0.00
            }
            product_lines.append(product_vals)
            total_extended_price += extended_price
            total_vat_amt += product.tax_amount
            grand_total += total
            amount_words = num2words(grand_total, to="currency", lang="ar")
            trans = Translator(from_lang="ar", to_lang="en")
            amount_words = trans.translate(amount_words)
        total_vals = {
            'total_extended_price': total_extended_price,
            'total_vat_amt': total_vat_amt,
            'grand_total': grand_total,
            'amount_words': amount_words,
        }
        total_amt_lst.append(total_vals)
        if not product_lines:
            raise ValidationError("Product Consume Part/Service tab not in products")

        datas = {
            # 'model': 'job.card.report',
            'jobs': job_lst,
            'product_lines': product_lines,
            'totals': total_amt_lst,
            'form_data': self.read()[0],
        }
        return self.env.ref('machine_repair_management.print_job_card_template_document').report_action(self,
                                                                                                        data=datas)

    '''This code is for Print Invoice Receipt'''

    def print_job_card_invoice(self):
        job_lst = []
        product_lines = []
        total_amt_lst = []
        total_extended_price = 0.00
        total_vat_amt = 0.00
        extended_price = 0.00
        grand_total = 0.00
        total = 0.00
        amount_words = False
        amount_words_en = False
        amount_words_ar = False
        move = self.env['account.move'].search([('name', '=', self.invoice_no)])
        if 'SAR' not in Num2Word_EN.CURRENCY_FORMS:
            Num2Word_EN.CURRENCY_FORMS['SAR'] = (
                ('riyal', 'riyals'),
                ('halala', 'halalas')
            )
        for job in self:
            vals = {
                'warehouse_id': job.warehouse_id.complete_name,
                'cic_ref_no': job.name,
                'partner_id': job.partner_id.name,
                'customer_name': job.customer_name or False,
                'address': job.address or False,
                'vat': job.partner_id.vat or False,
                'job_card_no': job.control_card_no or False,
                'remarks': job.supervisor_comments or False,
                'customer_no': job.warehouse_id.cst_no or False,
                'invoice_no': job.invoice_no or False,
                'invoice_date': job.invoice_date.strftime("%d-%m-%Y") if job.invoice_date else None,
                'sales_man': job.write_uid.name or False,
                'company_vat': self.env.company.vat or False,
                'qr_image': job.qr_image if job.qr_image else False,
                'building_no': job.building_number or None,
                'district': job.country_district_id.name or None,
                'city': job.customer_city_id.name or None,
                'country': job.country_id.name or None,
                'zipcode': job.zip_code or None,
                'additional_number': job.plot_identification or None,
                'street_name': '',
                'other_id': '',
                'company_name': self.env.company.name,
                'company_address': self.env.company.street or None,
                'company_building_number': '',
                'company_street_name': self.env.company.street2 or None,
                'company_district': self.env.company.state_id.name or None,
                'company_city': self.env.company.city or None,
                'company_country': self.env.company.country_id.name or None,
                'company_zip_code': self.env.company.zip or None,
                'company_additional_number': '',
                'company_vat': self.env.company.vat or None,
                'company_other_id': '',
                'name': job.name,
                'delivery_no': '',
                'control_card_no': self.control_card_no or ''

            }
            job_lst.append(vals)
        for product in self.product_line_ids:
            extended_price = product.price_unit
            # total = extended_price + product.tax_amount
            total = product.total
            product_vals = {
                'stock_group': self.product_category_id.name,
                # 'stock_group': product.product_id.categ_id.name,

                'stock_number': product.product_id.default_code,
                'description': product.product_id.name,
                'arabic_name': product.product_id.product_arabic_name or '',
                'qty': product.qty,
                'unit_price': product.price_unit,
                'unit_discount': '',
                'net_unit_price': product.price_unit,
                'extended_price': extended_price,
                'vat_percent': int(product.vat) if not product.under_warranty_bool else 0.00,
                'vat_amount': product.tax_amount if not product.under_warranty_bool else 0.00,
                'total': product.total if not product.under_warranty_bool else 0.00
            }
            product_lines.append(product_vals)
            total_extended_price += extended_price
            total_vat_amt += product.tax_amount
            grand_total += total
            amount_words_en = num2words(grand_total, to="currency", lang="en", currency="SAR")

            # Translate English words to Arabic
            trans = Translator(from_lang="en", to_lang="ar")
            # amount_words_ar = trans.translate(grand_total)
            amount_words_ar = trans.translate(amount_words_en)

        total_vals = {
            'total_extended_price': total_extended_price,
            'total_vat_amt': total_vat_amt,
            'grand_total': grand_total,
            'amount_words_en': amount_words_en,  # English
            'amount_words_ar': amount_words_ar,  # Arabic
        }
        # currently working  commeted on Sep 19 2025 by Vijaya bhaskar
        #     amount_words = num2words(grand_total, to="currency", lang="ar")
        #     trans = Translator(from_lang="ar", to_lang="en")
        #     amount_words = trans.translate(amount_words)
        # total_vals = {
        #     'total_extended_price': total_extended_price,
        #     'total_vat_amt': total_vat_amt,
        #     'grand_total': grand_total,
        #     'amount_words': amount_words,
        # }
        total_amt_lst.append(total_vals)
        if not product_lines:
            raise ValidationError("Product Consume Part/Service tab not in products")
        filename = f"Invoice_Details_{self.name}"
        # filename_encoded = urllib.parse.quote(filename)
        datas = {
            # 'model': 'job.card.report',
            'jobs': job_lst,
            'product_lines': product_lines,
            'totals': total_amt_lst,
            'form_data': self.read()[0],

        }
        return self.env.ref(
            'machine_repair_management.print_job_card_invoice_template_document'
        ).report_action(
            self,
            data=datas,
        )

        # return self.env.ref('machine_repair_management.print_job_card_invoice_template_document').report_action(self,data=datas)

        # return self.env.ref('machine_repair_management.print_job_card_invoice_template_document').with_context(
        #     report_name=filename
        # ).report_action(self, data=datas)
        # return self.env.ref('machine_repair_management.print_job_card_invoice_template_document').with_context(
        #         report_file_name=filename
        #     ).report_action(self, data=datas)
        #

        '''
        currently working commented by Vijaya Bhaskar on Aug-21-2025 due to file name is asked 
        return self.env.ref('machine_repair_management.print_job_card_invoice_template_document').report_action(self,data=datas)
        '''
        # if not self.whatsapp_invoice_sent:
        #     self.invoice_receipt_print_click = True
        #     try:
        #         # self.send_whatsapp_invoice_receipt()
        #         self.whatsapp_invoice_sent = True
        #     except Exception as e:
        #         _logger.error("Error sending WhatsApp invoice: %s", str(e))
        #         raise ValidationError(f"Failed to send invoice via WhatsApp: {str(e)}")
        #     finally:
        #         self.invoice_receipt_print_click = False
        #

        # Render PDF
        # try:
        #     return self.env.ref('machine_repair_management.print_job_card_invoice_template_document').report_action(self, data=datas)
        # except Exception as e:
        #     _logger.error("Error rendering PDF: %s", str(e))
        #     raise ValidationError(f"Failed to generate PDF: {str(e)}")

        # return {
        #     'effect':{
        #         'type': 'rainbow_man',
        #         'fadeout':'slow',
        #         'message' : 'Your Invoice Receipt send Successfully to Customer Whatsapp Number',
        #         }
        #     }

    # def _get_report_base_filename(self):
    #     """Used by Odoo to build the download filename."""
    #     self.ensure_one()
    #     def clean(s):
    #         # keep safe chars only: letters, numbers, dot, underscore, dash
    #         return re.sub(r'[^A-Za-z0-9._-]+', '_', (s or '').strip())
    #
    #     base = f"Invoice_Details_{clean(self.name) or 'Task'}"
    #     if getattr(self, 'invoice_no', False):
    #         base += f"_{clean(self.invoice_no)}"
    #     return base

    qr_image = fields.Binary("QR Code", compute='_generate_qr_code')
    qr_in_report = fields.Boolean('Display QRCode in Report?')

    def _generate_qr_code(self):
        self.qr_image = None
        for order in self:
            supplier_name = order.company_id.name or "N/A"
            vat = str(order.company_id.vat or "N/A")  # Handle False or empty VAT
            vat_total = str(order.parts_grand_total_amount or 0.0)
            date = str(order.service_created_datetime or fields.Datetime.now())

            # Format invoice details for QR code
            lf = '\t'
            invoice = lf.join([
                'Seller name:', supplier_name,
                'Vat Registration Number:', vat,
                'Date:', date,
                'VAT total:', vat_total
            ])

            # Generate QR code
            qr_img = generate_qr_code(invoice)
            order.write({
                'qr_image': qr_img
            })
        return True

    '''this code is for service Charges receipt print'''

    def print_job_card_receipt(self):
        self.ensure_one()
        job_lst = []
        product_lines = []
        total_amt_lst = []
        total_extended_price = 0.00
        total_vat_amt = 0.00
        extended_price = 0.00
        grand_total = 0.00
        total = 0.00
        amount_words = False
        inspection_charges_amount_received = 0.0
        balance_paid = 0.0
        move = self.env['account.move'].search([('name', '=', self.invoice_no)])
        for job in self:
            vals = {
                'warehouse_id': job.warehouse_id.name,
                'cic_ref_no': job.control_card_no or '',
                'partner_id': job.partner_id.name,
                'customer_name': job.customer_name or '',
                'address': job.address or False,
                'vat': job.partner_id.vat or False,
                'job_card_no': job.name,
                'remarks': job.supervisor_comments,
                'customer_no': job.warehouse_id.cst_no or False,
                # 'invoice_no': job.invoice_no or False,
                # 'invoice_date': move.invoice_date or False,
                'sales_man': job.write_uid.name or '',
                'company_vat': self.env.company.vat or '',
                'technician_name': job.team_id.name or '',
                # # Client asked Proforma invoice date is today date on august-26-2025
                'invoice_date': fields.Datetime.today(),
                'invoice_no': job.name or '',
                'model_no': job.product_id.default_code or None,
                'serial_no': job.product_slno or None,

            }
            job_lst.append(vals)
        for product in self.product_line_ids:
            extended_price = product.price_unit
            # total = extended_price + product.tax_amount
            total = product.total
            product_vals = {
                'stock_group': product.product_id.categ_id.name,
                'stock_number': product.product_id.default_code,
                'description': product.product_id.name,
                'qty': product.qty,
                'unit_price': product.price_unit,
                'unit_discount': '',
                'net_unit_price': product.price_unit,
                'extended_price': extended_price,
                'vat_percent': int(product.vat) if not product.under_warranty_bool else 0.00,
                'vat_amount': product.tax_amount if not product.under_warranty_bool else 0.00,
                'total': product.total if not product.under_warranty_bool else 0.00
            }
            product_lines.append(product_vals)
            total_extended_price += extended_price
            total_vat_amt += product.tax_amount
            grand_total += total

        # if self.inspection_charges_bool:
        #     grand_total -= self.inspection_charges_amount
        # if not self.balance_amount_received_bool:
        #     grand_total -= self.inspection_charges_amount

        inspection_charges_amount_received = self.final_inspection_charges_amount
        balance_paid = self.balance_paid
        amount_words = num2words(grand_total, to="currency", lang="ar")

        # if balance_paid != 0:
        #     amount_words = num2words(balance_paid, to="currency", lang="ar")
        # elif inspection_charges_amount_received != 0:
        #     amount_words = num2words(inspection_charges_amount_received, to="currency", lang="ar")
        # else:
        #     amount_words = num2words(grand_total, to="currency", lang="ar")

        trans = Translator(from_lang="ar", to_lang="en")
        amount_words = trans.translate(amount_words)
        total_vals = {
            'total_extended_price': total_extended_price,
            'total_vat_amt': total_vat_amt,
            'grand_total': grand_total,
            'amount_words': amount_words,
            'inspection_charges_amount_received': inspection_charges_amount_received,
            'balance_paid': balance_paid
        }
        total_amt_lst.append(total_vals)
        if not product_lines:
            raise ValidationError("Product Consume Part/Service tab not in products")
        datas = {
            # 'model': 'job.card.report',
            'jobs': job_lst,
            'product_lines': product_lines,
            'totals': total_amt_lst,
            'form_data': self.read()[0],
        }

        return self.env.ref('machine_repair_management.print_job_card_receipt_template_document').report_action(self,
                                                                                                                data=datas)

    '''this code is for Inspection Charges receipt print(ie.150)'''

    def print_inspection_charge_receipt(self):
        job_lst = []
        product_lines = []
        total_amt_lst = []
        total_extended_price = 0.00
        total_vat_amt = 0.00
        extended_price = 0.00
        grand_total = 0.00
        total = 0.00
        amount_words = False
        inspection_amount = 0.0
        inspection_amount_without_tax = 0.0

        inspection_description = self.env['ir.config_parameter'].sudo().get_param(
            'machine_repair_management.inspection_charges_description')

        inspection_code = self.env['ir.config_parameter'].sudo().get_param(
            'machine_repair_management.inspection_charges_code')
        # quote = self.env['sale.order'].search([('task_id.name', '=', self.name)])
        for job in self:
            address = f"{job.address or ' '}, {job.partner_id.mobile or ''}, {job.partner_id.vat or ''}"

            user_tz = self.env.user.tz or 'UTC'
            user_timezone = pytz.timezone(user_tz)
            local_dt = pytz.utc.localize(job.service_created_datetime).astimezone(user_timezone)

            local_date = local_dt.date()
            local_time = local_dt.strftime('%H:%M:%S')

            # address = job.address + ' ,' + job.partner_id.mobile + ' ,' + job.partner_id.vat
            vals = {
                'receipt_no': job.control_card_no or '',
                'partner_id': job.partner_id.name or '',
                'customer_name': job.customer_name or '',
                'address': job.address or False,
                # 'contact_no' : job.partner_id.mobile,
                'job_card_no': job.control_card_no or '',
                'remarks': job.supervisor_comments,
                # 'quotation_no' : quote.name,
                # 'date' : f"{local_date}{local_time}",
                'date': local_dt.strftime("%d-%m-%Y %H:%M:%S"),
                # 'date' : job.service_created_datetime.strftime("%d-%M-%Y %H:%M:%S"),
                # 'quotation_expiry_date' : quote.validity_date,
                'technician_name': job.technician_id.name,
                'company_vat': self.env.company.vat or '',
                'product_category': job.product_category_id.name or '',
                'product': job.product_id.name or '',
                'model': job.model or '',
                'serial_no': job.product_slno or '',
                'phone': job.phone or '',

            }
            job_lst.append(vals)

            inspection_amount = job.inspection_charges_amount

            inspection_amount_without_tax = inspection_amount / (1 + (15 / 100))

            # for product in self.product_line_ids:
            #     extended_price = product.price_unit
            #     total = extended_price + product.tax_amount
            product_vals = {
                'stock_group': '',
                'stock_number': inspection_code,
                'description': inspection_description,
                'qty': 1,
                'unit_price': inspection_amount_without_tax,
                'unit_discount': '',
                'net_unit_price': inspection_amount_without_tax,
                'extended_price': inspection_amount_without_tax,
                'vat_percent': '15',
                'vat_amount': inspection_amount - inspection_amount_without_tax,
                'total': inspection_amount
            }
            product_lines.append(product_vals)
            total_extended_price += inspection_amount_without_tax
            total_vat_amt += inspection_amount - inspection_amount_without_tax

            grand_total += inspection_amount
            amount_words = num2words(grand_total, to="currency", lang="ar")
            trans = Translator(from_lang="ar", to_lang="en")
            amount_words = trans.translate(amount_words)
        total_vals = {
            'total_extended_price': total_extended_price,
            'total_vat_amt': total_vat_amt,
            'grand_total': grand_total,
            'amount_words': amount_words,
        }
        total_amt_lst.append(total_vals)
        if not product_lines:
            raise ValidationError("Product Consume Part/Service tab not in products")
        datas = {
            # 'model': 'job.card.report',
            'jobs': job_lst,
            'product_lines': product_lines,
            'inspection_description': inspection_description or '',
            'inspection_code': inspection_code or '',
            'totals': total_amt_lst,
            'form_data': self.read()[0],
            'inspection_amount_without_tax': inspection_amount_without_tax or '',
            'inspection_amount': inspection_amount,
            'receipt_no': self.payment_receipt_id.name,
            'name': self.name
        }
        # self.send_whatsapp_inspection_receipt()
        # return self.env.ref('machine_repair_management.print_inspection_charge_receipt_template_document').report_action(self,data=datas)
        _logger.info("Data prepared for PDF rendering: %s", datas)

        # Render PDF
        try:
            return self.env.ref(
                'machine_repair_management.print_inspection_charge_receipt_template_document').report_action(self,
                                                                                                             data=datas)
        except Exception as e:
            _logger.error("Error rendering PDF: %s", str(e))
            raise ValidationError(f"Failed to generate PDF: {str(e)}")

        # self.env.ref('machine_repair_management.print_inspection_charge_receipt_template_document').report_action(self,data=datas)

    '''Added on Sep 17-2025 by Vijaya Bhaskar'''

    def preformatted_job_card_cash_receipt(self):
        self.ensure_one()
        job_lst = []
        product_lines = []
        total_amt_lst = []
        total_extended_price = 0.00
        total_vat_amt = 0.00
        extended_price = 0.00
        grand_total = 0.00
        total = 0.00
        amount_words = False
        for job in self:
            vals = {
                'warehouse_id': job.warehouse_id.name,
                'cic_ref_no': job.control_card_no,
                'partner_id': job.partner_id.name,
                'customer_name': job.customer_name or '',
                'address': job.address,
                'vat': job.partner_id.vat,
                'job_card_no': job.name,
                'engineer_comments': job.engineer_comments,
                'service_created_date': job.service_created_datetime.strftime(
                    "%d-%m-%Y %H:%M:%S") if job.service_created_datetime else None,
                'completed_date_time': job.closed_datetime.strftime(
                    "%d-%m-%Y %H:%M:%S") if job.closed_datetime else None,
                'model_no': job.product_id.default_code or None,
                'serial_no': job.product_slno or None,
                'technician_name': job.technician_id.name,
                'company_vat': self.env.company.vat,

            }
            job_lst.append(vals)
        for product in self.product_line_ids:
            extended_price = product.price_unit
            total = product.total
            # total = extended_price + product.tax_amount

            product_vals = {
                'stock_group': product.product_id.categ_id.name,
                'stock_number': product.product_id.default_code,
                'description': product.product_id.name,
                'qty': product.qty,
                'unit_price': product.price_unit,
                'unit_discount': '',
                'net_unit_price': product.price_unit,
                'extended_price': extended_price,
                'vat_percent': product.vat if not product.under_warranty_bool else 0.00,
                'vat_amount': product.tax_amount if not product.under_warranty_bool else 0.00,
                'total': product.total if not product.under_warranty_bool else 0.00
            }
            product_lines.append(product_vals)
            total_extended_price += extended_price
            total_vat_amt += product.tax_amount
            grand_total += total
            amount_words = num2words(grand_total, to="currency", lang="ar")
            trans = Translator(from_lang="ar", to_lang="en")
            amount_words = trans.translate(amount_words)
        total_vals = {
            'total_extended_price': total_extended_price,
            'total_vat_amt': total_vat_amt,
            'grand_total': grand_total,
            'amount_words': amount_words,
        }
        total_amt_lst.append(total_vals)
        if not product_lines:
            raise ValidationError("Product Consume Part/Service tab not in products")

        datas = {
            'service_jobs': job_lst,
            'product_lines': product_lines,
            'totals': total_amt_lst,
            'form_data': self.read()[0],
        }

        return self.env.ref('machine_repair_management.service_cash_receipt_report').report_action(self, data=datas)

    '''Added on Oct 24 By Gokul...'''

    def job_card_service_report(self):
        self.ensure_one()
        job_lst = []
        product_lines = []
        total_amt_lst = []
        total_extended_price = 0.00
        total_vat_amt = 0.00
        extended_price = 0.00
        grand_total = 0.00
        total = 0.00
        amount_words = False
        job_lst_symptoms = []
        job_lst_defects = []
        job_lst_services = []

        for job in self.symptoms_line_ids:
            vals = {
                'symptoms_id': job.code.sym_desc,
            }
            job_lst_symptoms.append(vals)
        for job in self.defects_type_ids:
            vals = {
                'defects_id': job.code.def_desc,
            }
            job_lst_defects.append(vals)
        for job in self.service_type_ids:
            vals = {
                'services_id': job.code.name,
            }
            job_lst_services.append(vals)
        for job in self:
            signature_data = None
            if job.signature:
                try:
                    # If it's already a string, use it directly
                    if isinstance(job.signature, str):
                        signature_data = job.signature
                    # If it's bytes, decode it
                    elif isinstance(job.signature, bytes):
                        signature_data = job.signature.decode('utf-8')
                    else:
                        # Try to convert to string
                        signature_data = str(job.signature)
                except Exception as e:
                    _logger.warning("Failed to process signature: %s", str(e))
                    signature_data = None

            local_app_start_time = False
            local_closed_date_time = False

            user_tz = self.env.user.tz or 'UTC'
            user_timezone = pytz.timezone(user_tz)
            local_service_created_datetime = pytz.utc.localize(job.service_created_datetime).astimezone(user_timezone)
            if job.planned_date_begin:
                local_app_start_time = pytz.utc.localize(job.planned_date_begin).astimezone(user_timezone)
            if job.closed_datetime:
                local_closed_date_time = pytz.utc.localize(job.closed_datetime).astimezone(user_timezone)

            vals = {
                'warehouse_id': job.warehouse_id.name,
                'cic_ref_no': job.control_card_no,
                'partner_id': job.partner_id.name,
                'customer_name': job.customer_name or '',
                'address': job.address,
                'vat': job.partner_id.vat,
                'job_card_no': job.name,
                'engineer_comments': job.engineer_comments if job.job_card_state_code != '117' and job.engineer_comments else f"Unit Pull Out - {job.engineer_comments}" if job.engineer_comments else None,
                # 'service_created_date': job.service_created_datetime.strftime(
                #     "%d-%m-%Y %H:%M:%S") if job.service_created_datetime else None,
                'service_created_date': local_service_created_datetime.strftime(
                    "%d-%m-%Y %H:%M:%S") if job.service_created_datetime else None,
                'completed_date_time': job.closed_datetime.strftime(
                    "%d-%m-%Y %H:%M:%S") if job.closed_datetime else None,
                'model_no': job.product_id.default_code or None,
                'serial_no': job.product_slno or None,
                'technician_name': job.technician_id.name,
                'company_vat': self.env.company.vat,
                # 'signature': job.signature,
                'services_warranty': job.service_warranty_id.name,
                'dealer_name': job.dealer_id.name,
                'invoice_no': job.purchase_invoice_no,
                'invoice_date': job.purchase_date.strftime("%d-%m-%Y") if job.purchase_date else None,
                'technician_first_visit': job.technician_first_visit_id.name or None,
                'first_visit_date': job.technician_first_visit_date.strftime(
                    "%d-%m-%Y") if job.technician_first_visit_date else None,
                'first_vist_time_in': job.technician_first_intime if job.technician_first_intime else None,
                'first_vist_time_out': job.technician_first_outtime if job.technician_first_outtime else None,
                'technician_second_visit': job.technician_second_visit_id.name if job.technician_second_visit_id else None,
                'second_visit_date': job.technician_second_visit_date.strftime(
                    "%d-%m-%Y") if job.technician_second_visit_date else None,
                'second_visit_time_in': job.technician_second_intime if job.technician_second_intime else None,
                'second_visit_time_out': job.technician_second_outtime if job.technician_second_outtime else None,
                'customer_mob_no': job.phone,
                'customer_VAT_no': job.customer_identification_number or '',
                'engineer_comments_second': job.engineer_comments_second or '',
                'promised_date_time': local_app_start_time.strftime(
                    "%d-%m-%Y %H:%M:%S") if job.planned_date_begin else None,
                'second_visit_technician_bool': job.second_visit_technician_bool,
                'client_comments': job.client_comments if job.client_comments else None,
                'volt': job.volt,
                'ampere': job.ampere,
                'lp': job.lp,
                'hp': job.hp,
                'sat': job.sat,
                'rat': job.rat,
                'length': job.length,
                'width': job.width,
                'area': job.area,
                'p_length': job.p_length,
                'work_center_id': job.work_center_id.name if job.work_center_id else None,
                'signature': signature_data,
                'closed_date_time': local_closed_date_time.strftime(
                    "%d-%m-%Y %H:%M:%S") if job.closed_datetime else None,
                # Add this line

            }
            job_lst.append(vals)

        for product in self.product_line_ids:
            extended_price = product.price_unit
            total = product.total
            # total = extended_price + product.tax_amount

            product_vals = {
                'stock_group': product.product_id.categ_id.name,
                'stock_number': product.product_id.default_code,
                'description': product.product_id.name,
                'qty': product.qty,
                'unit_price': product.price_unit,
                'unit_discount': '',
                'net_unit_price': product.price_unit,
                'extended_price': extended_price,
                'vat_percent': product.vat if not product.under_warranty_bool else 0.00,
                'vat_amount': product.tax_amount if not product.under_warranty_bool else 0.00,
                'total': product.total if not product.under_warranty_bool else 0.00
            }
            product_lines.append(product_vals)
            total_extended_price += extended_price
            total_vat_amt += product.tax_amount
            grand_total += total
            amount_words = num2words(grand_total, to="currency", lang="ar")
            trans = Translator(from_lang="ar", to_lang="en")
            amount_words = trans.translate(amount_words)
        total_vals = {
            'total_extended_price': total_extended_price,
            'total_vat_amt': total_vat_amt,
            'grand_total': grand_total,
            'amount_words': amount_words,
        }
        total_amt_lst.append(total_vals)
        # if not product_lines:
        #     raise ValidationError("Product Consume Part/Service tab not in products")

        datas = {
            'service_jobs': job_lst,
            'symptoms': job_lst_symptoms,
            'defects': job_lst_defects,
            'services': job_lst_services,
            'product_lines': product_lines,
            'totals': total_amt_lst,
            'form_data': self.read()[0],
            # 'name':self.name,
            # 'signature_sign':self.signature,
            # 'signature':self.signature,
        }

        return self.env.ref('machine_repair_management.service_job_card_report').report_action(self, data=datas)

    '''Added By Vijaya Bhaskar on Sep 1 2025 Job Card Service report '''
    # def job_card_service_report(self):
    #     self.ensure_one()
    #     job_lst = []
    #     product_lines = []
    #     total_amt_lst = []
    #     total_extended_price = 0.00
    #     total_vat_amt = 0.00
    #     extended_price = 0.00
    #     grand_total = 0.00
    #     total = 0.00
    #     amount_words = False
    #     for job in self:
    #         vals = {
    #             'warehouse_id': job.warehouse_id.name,
    #             'cic_ref_no': job.control_card_no,
    #             'partner_id': job.partner_id.name,
    #             'customer_name':job.customer_name or '',
    #             'address': job.address,
    #             'vat': job.partner_id.vat,
    #             'job_card_no': job.name,
    #             'engineer_comments': job.engineer_comments,
    #             'service_created_date': job.service_created_datetime.strftime("%d-%m-%Y %H:%M:%S") if job.service_created_datetime else None,
    #             'completed_date_time':job.closed_datetime.strftime("%d-%m-%Y %H:%M:%S") if job.closed_datetime else None,
    #             'model_no':job.product_id.default_code or None,
    #             'serial_no':job.product_slno or None,
    #             'technician_name': job.technician_id.name,
    #             'company_vat': self.env.company.vat,
    #              'signature': job.signature,  # Add this line
    #
    #         }
    #         job_lst.append(vals)
    #
    #     for product in self.product_line_ids:
    #         extended_price = product.price_unit
    #         total = product.total
    #         # total = extended_price + product.tax_amount
    #
    #         product_vals = {
    #             'stock_group': product.product_id.categ_id.name,
    #             'stock_number': product.product_id.default_code,
    #             'description': product.product_id.name,
    #             'qty': product.qty,
    #             'unit_price': product.price_unit,
    #             'unit_discount': '',
    #             'net_unit_price': product.price_unit,
    #             'extended_price': extended_price,
    #             'vat_percent': product.vat if not product.under_warranty_bool else 0.00,
    #             'vat_amount': product.tax_amount if not product.under_warranty_bool else 0.00,
    #             'total': product.total if not product.under_warranty_bool else 0.00
    #         }
    #         product_lines.append(product_vals)
    #         total_extended_price += extended_price
    #         total_vat_amt += product.tax_amount
    #         grand_total += total
    #         amount_words = num2words(grand_total, to="currency", lang="ar")
    #         trans = Translator(from_lang="ar", to_lang="en")
    #         amount_words = trans.translate(amount_words)
    #     total_vals = {
    #         'total_extended_price': total_extended_price,
    #         'total_vat_amt': total_vat_amt,
    #         'grand_total': grand_total,
    #         'amount_words': amount_words,
    #     }
    #     total_amt_lst.append(total_vals)
    #     if not product_lines:
    #         raise ValidationError("Product Consume Part/Service tab not in products")
    #
    #     datas = {
    #         'service_jobs': job_lst,
    #         'product_lines': product_lines,
    #         'totals': total_amt_lst,
    #         'form_data': self.read()[0],
    #         # 'signature':self.signature,
    #     }
    #
    #     return self.env.ref('machine_repair_management.service_job_card_report').report_action(self,data=datas)
    #

    '''Added on Sep 17-2025 by Vijaya Bhaskar'''

    def preformatted_job_card_cash_receipt(self):
        self.ensure_one()
        job_lst = []
        product_lines = []
        total_amt_lst = []
        total_extended_price = 0.00
        total_vat_amt = 0.00
        extended_price = 0.00
        grand_total = 0.00
        total = 0.00
        amount_words = False
        for job in self:
            vals = {
                'warehouse_id': job.warehouse_id.name,
                'cic_ref_no': job.control_card_no,
                'partner_id': job.partner_id.name,
                'customer_name': job.customer_name or '',
                'address': job.address,
                'vat': job.partner_id.vat,
                'job_card_no': job.name,
                'engineer_comments': job.engineer_comments,
                'service_created_date': job.service_created_datetime.strftime(
                    "%d-%m-%Y %H:%M:%S") if job.service_created_datetime else None,
                'completed_date_time': job.closed_datetime.strftime(
                    "%d-%m-%Y %H:%M:%S") if job.closed_datetime else None,
                'model_no': job.product_id.default_code or None,
                'serial_no': job.product_slno or None,
                'technician_name': job.technician_id.name,
                'company_vat': self.env.company.vat,

            }
            job_lst.append(vals)
        for product in self.product_line_ids:
            extended_price = product.price_unit
            total = product.total
            # total = extended_price + product.tax_amount

            product_vals = {
                'stock_group': product.product_id.categ_id.name,
                'stock_number': product.product_id.default_code,
                'description': product.product_id.name,
                'qty': product.qty,
                'unit_price': product.price_unit,
                'unit_discount': '',
                'net_unit_price': product.price_unit,
                'extended_price': extended_price,
                'vat_percent': product.vat if not product.under_warranty_bool else 0.00,
                'vat_amount': product.tax_amount if not product.under_warranty_bool else 0.00,
                'total': product.total if not product.under_warranty_bool else 0.00
            }
            product_lines.append(product_vals)
            total_extended_price += extended_price
            total_vat_amt += product.tax_amount
            grand_total += total
            amount_words = num2words(grand_total, to="currency", lang="ar")
            trans = Translator(from_lang="ar", to_lang="en")
            amount_words = trans.translate(amount_words)
        total_vals = {
            'total_extended_price': total_extended_price,
            'total_vat_amt': total_vat_amt,
            'grand_total': grand_total,
            'amount_words': amount_words,
        }
        total_amt_lst.append(total_vals)
        if not product_lines:
            raise ValidationError("Product Consume Part/Service tab not in products")

        datas = {
            'service_jobs': job_lst,
            'product_lines': product_lines,
            'totals': total_amt_lst,
            'form_data': self.read()[0],
        }

        return self.env.ref('machine_repair_management.service_cash_receipt_report').report_action(self, data=datas)

    ''' This code for send whatsapp to customer for inspection charge receipt on June - 11- 2025  '''

    # def send_whatsapp_inspection_receipt(self):
    #
    #     phone_number = self.phone
    #     if not phone_number:
    #         _logger.info("❌ No Phone Number is linked")
    #         return
    #
    #
    #     phone_number = phone_number.replace('+', '').replace(' ', '')
    #     try:
    #         pdf_content = False
    #         if self.service_charge_receipt_print_click:
    #             pdf_content, _ = self.env['ir.actions.report'].sudo()._render_qweb_pdf(
    #                 'machine_repair_management.print_job_card_receipt_template_document', [self.id],
    #                 data=self.print_inspection_charge_receipt().get('data', {})
    #                 )
    #
    #         elif self.invoice_receipt_print_click:
    #             pdf_content, _ = self.env['ir.actions.report'].sudo()._render_qweb_pdf(
    #                 'machine_repair_management.print_job_card_invoice_template_document', [self.id],
    #                 data=self.print_inspection_charge_receipt().get('data', {})
    #                 )
    #         elif self.inspection_charges_receipt_click:
    #             # pdf_content, _ = self.env['ir.actions.report'].sudo()._render_qweb_pdf('machine_repair_management.print_inspection_charge_receipt_template_document',[self.id])
    #             pdf_content, _ = self.env['ir.actions.report'].sudo()._render_qweb_pdf(
    #             'machine_repair_management.print_inspection_charge_receipt_template_document', [self.id],
    #             data=self.print_inspection_charge_receipt().get('data', {})
    #             )
    #         _logger.info("✅ PDF generated for Job order %s",self.name)
    #
    #     except Exception as e:
    #         _logger.info("Error rendering PDF for order %s: %s", self.name, str(e))
    #
    #     file_name = False
    #     if self.service_charge_receipt_print_click:
    #
    #         file_name = f"Service Charges Receipt{self.name}.pdf"
    #
    #     elif self.invoice_receipt_print_click:
    #
    #         file_name = f"Invoice Receipt {self.invoice_no}.pdf"
    #
    #     elif self.inspection_charges_receipt_click:
    #         file_name = f"Inspection Charges Receipt {self.name}.pdf"
    #
    #     media_id = self._upload_pdf_meta(pdf_content,file_name)
    #     if not media_id:
    #         _logger.info("❌ Failed to upload the media id %s",self.name)
    #         return
    #
    #     self.send_pdf_to_whatsapp(phone_number,media_id, file_name, self.name)

    def send_whatsapp_inspection_receipt(self):
        # if not self.whatsapp_send_bool:
        #     _logger.info("❌ No WhatsApp set in res Config Settings")
        #     return False
        if not self.env['ir.config_parameter'].sudo().get_param(
                'machine_repair_management.whatsapp_send_bool') == 'True':
            _logger.info("❌ No WhatsApp set in res Config Settings")
            return False

        phone_number = self.phone
        country_code = self.country_id.phone_code

        if not phone_number:
            _logger.info("❌ No Phone Number is linked")
            return
        phone_number = phone_number.replace('+', '').replace(' ', '')
        phone_number = f"{country_code}{phone_number}"

        whatsapp_opt_in = self.whatsapp_opt_in

        if not whatsapp_opt_in:
            _logger.info("❌ No WhatsApp opt-in for Customer %s", self.customer_name)
            return False

        pdf_content = False
        try:
            # pdf_content, _ = self.env['ir.actions.report'].sudo()._render_qweb_pdf('machine_repair_management.print_inspection_charge_receipt_template_document',[self.id])

            # pdf_content, _ = self.env['ir.actions.report'].sudo()._render_qweb_pdf(
            # 'machine_repair_management.print_inspection_charge_receipt_template_document', [self.id],
            # data=self.print_inspection_charge_receipt().get('data', {})
            # )

            datas = self.print_inspection_charge_receipt().get('data', {})
            pdf_content, _ = self.env['ir.actions.report'].sudo()._render_qweb_pdf(
                'machine_repair_management.print_inspection_charge_receipt_template_document',
                [self.id],
                data=datas
            )
            _logger.info("✅ PDF generated for Job order %s", self.name)

        except Exception as e:
            _logger.info("Error rendering PDF for order %s: %s", self.name, str(e))

        file_name = f"Inspection Charges Receipt {self.name}.pdf"
        media_id = self._upload_pdf_meta(pdf_content, file_name)
        if not media_id:
            _logger.info("❌ Failed to upload the media id %s", self.name)
            return

        self.send_pdf_to_whatsapp(phone_number, media_id, file_name, self.name)

    ''' Working code Commented on Oct-15-2025 due to Proforma Invoice Add  Extra Message
    def send_whatsapp_service_charges_receipt(self):
        if not self.whatsapp_send_bool:
            _logger.info("❌ No WhatsApp set in res Config Settings")
            return False

        phone_number = self.phone
        country_code = self.country_id.phone_code

        if not phone_number:
            _logger.info("❌ No Phone Number is linked")
            return
        phone_number = phone_number.replace('+', '').replace(' ', '')
        phone_number = f"{country_code}{phone_number}"

        whatsapp_opt_in = self.whatsapp_opt_in

        if not whatsapp_opt_in:
            _logger.info("❌ No WhatsApp opt-in for Customer %s", self.customer_name)
            return False

        pdf_content = False    
        try:
            # pdf_content, _ = self.env['ir.actions.report'].sudo()._render_qweb_pdf('machine_repair_management.print_inspection_charge_receipt_template_document',[self.id])
            datas = self.print_job_card_receipt().get('data', {})
            pdf_content, _ = self.env['ir.actions.report'].sudo()._render_qweb_pdf(
                'machine_repair_management.print_job_card_receipt_template_document',
                [self.id],
                data=datas
            )
            _logger.info("PDF generated for job card %s", self.name)
        except Exception as e:
            _logger.error("Error rendering PDF for job card %s: %s", self.name, str(e))
            raise ValidationError(f"Failed to generate PDF: {str(e)}")

        file_name = f"PRO-FORMA Invoice {self.name}.pdf"
        media_id = self._upload_pdf_meta(pdf_content, file_name)
        if not media_id:
            _logger.info("❌ Failed to upload the media id %s", self.name)
            return

        self.send_pdf_to_whatsapp(phone_number, media_id, file_name, self.name)

        return {
            'effect':{
                'type': 'rainbow_man',
                'fadeout':'slow',
                'message': 'Your PRO-FORMA Invoice send Successfully to Customer Whatsapp Number',
                }
            }

    '''

    def send_whatsapp_service_charges_receipt(self):
        # if not self.whatsapp_send_bool:
        #     _logger.info("❌ No WhatsApp set in res Config Settings")
        #     return False
        if not self.env['ir.config_parameter'].sudo().get_param(
                'machine_repair_management.whatsapp_send_bool') == 'True':
            _logger.info("❌ No WhatsApp set in res Config Settings")
            return False

        phone_number = self.phone
        country_code = self.country_id.phone_code

        if not phone_number:
            _logger.info("❌ No Phone Number is linked")
            return False

        phone_number = phone_number.replace('+', '').replace(' ', '')
        phone_number = f"{country_code}{phone_number}"

        if not self.whatsapp_opt_in:
            _logger.info("❌ No WhatsApp opt-in for Customer %s", self.customer_name)
            return False

        whatsapp_phone_number_id = self.env['ir.config_parameter'].sudo().get_param(
            'whatsapp_sale_order_notify.whatsapp_phone_number_id')
        access_token = self.env['ir.config_parameter'].sudo().get_param(
            'whatsapp_sale_order_notify.whatsapp_access_token')

        if not access_token or not whatsapp_phone_number_id:
            _logger.error("❌ WhatsApp configuration missing")
            return False

        base_url = f'https://graph.facebook.com/v18.0/{whatsapp_phone_number_id}'
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }

        # --- Step 1: Send WhatsApp Text Message ---

        # message = (
        #     f"Dear {self.customer_name},\n\n"
        #     f"Please find attached your Proforma Invoice {self.name}.\n"
        #     f"Kindly review the details and proceed with the necessary actions.\n\n"
        #     f"HH-Shaker – Service Team"
        # )

        message = (
            f"عزيزي {self.customer_name}،\n"
            f"مرفق لكم الفاتورة المبدئية رقم {self.name}\n"
            "نرجو منكم مراجعة التفاصيل واتخاذ الإجراءات اللازمة.\n"
            "------------------------------------------------------\n"
            f"Dear {self.customer_name},\n"
            f"Please find attached the Pro-Forma Invoice No. {self.name}.\n"
            "Kindly review the details and take the necessary actions.\n"
            "HH-Shaker – Service Team"
        )

        template_payload = {
            'messaging_product': "whatsapp",
            'to': phone_number,
            'type': "text",
            'text': {'body': message},
        }

        try:
            response = requests.post(f"{base_url}/messages", headers=headers, json=template_payload)
            response.raise_for_status()
            _logger.info("✅ WhatsApp text message sent successfully to %s", phone_number)
        except requests.exceptions.RequestException as e:
            _logger.error("❌ Failed to send WhatsApp message: %s", str(e))
            return False

        # --- Step 2: Generate PDF ---
        try:
            datas = self.print_job_card_receipt().get('data', {})
            pdf_content, _ = self.env['ir.actions.report'].sudo()._render_qweb_pdf(
                'machine_repair_management.print_job_card_receipt_template_document',
                [self.id],
                data=datas
            )
            _logger.info("📄 PDF generated successfully for job card %s", self.name)
        except Exception as e:
            _logger.error("❌ Error rendering PDF for job card %s: %s", self.name, str(e))
            raise ValidationError(f"Failed to generate PDF: {str(e)}")

        # --- Step 3: Upload and Send PDF ---
        file_name = f"PRO-FORMA Invoice {self.name}.pdf"
        media_id = self._upload_pdf_meta(pdf_content, file_name)

        if not media_id:
            _logger.info("❌ Failed to upload PDF for %s", self.name)
            return False

        try:
            self.send_pdf_to_whatsapp(phone_number, media_id, file_name, self.name)
            _logger.info("✅ PDF sent successfully to WhatsApp for %s", phone_number)
        except Exception as e:
            _logger.error("❌ Failed to send PDF to WhatsApp: %s", str(e))
            return False
        # self._send_whatsapp_job_card_report_for_ready_to_invoice()
        # self.send_whatsapp_invoice_receipt()
        return {
            'effect': {
                'type': 'rainbow_man',
                'fadeout': 'slow',
                'message': 'Your PRO-FORMA Invoice was sent successfully to the customer via WhatsApp.',
            }
        }

    def send_whatsapp_invoice_receipt(self):
        # if not self.whatsapp_send_bool:
        #     _logger.info("❌ No WhatsApp set in res Config Settings")
        #     return False
        if not self.env['ir.config_parameter'].sudo().get_param(
                'machine_repair_management.whatsapp_send_bool') == 'True':
            _logger.info("❌ No WhatsApp set in res Config Settings")
            return False

        phone_number = self.phone
        country_code = self.country_id.phone_code

        if not phone_number:
            _logger.info("❌ No Phone Number is linked")
            return False

        phone_number = phone_number.replace('+', '').replace(' ', '')
        phone_number = f"{country_code}{phone_number}"

        if not self.whatsapp_opt_in:
            _logger.info("❌ No WhatsApp opt-in for Customer %s", self.customer_name)
            return False

        whatsapp_phone_number_id = self.env['ir.config_parameter'].sudo().get_param(
            'whatsapp_sale_order_notify.whatsapp_phone_number_id')
        access_token = self.env['ir.config_parameter'].sudo().get_param(
            'whatsapp_sale_order_notify.whatsapp_access_token')

        if not access_token or not whatsapp_phone_number_id:
            _logger.error("❌ WhatsApp configuration missing")
            return False

        base_url = f'https://graph.facebook.com/v18.0/{whatsapp_phone_number_id}'
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }

        # --- Step 1: Send WhatsApp Text Message ---
        # message = (
        #     f"Dear {self.customer_name},\n\n"
        #     f"Please find attached the Invoice {self.invoice_no}.\n"
        #     f"Thank you for your business,\nHH-Shaker – Service Team"
        # )

        message = (
            f"عزيزي {self.customer_name},\n"
            f"نرفق لكم الفاتورة ({self.invoice_no or ''}) الخاصة بالخدمة المطلوبة.\n"
            f"شكراً لتعاونكم.\n"
            "---------------------------------------------------------\n"
            f"Dear {self.customer_name},\n"
            f"Please find attached Invoice ({self.invoice_no or ''}) for the requested service.\n"
            f"Thank you for your cooperation.\n"
            "HH-Shaker – Service Team"
        )

        template_payload = {
            'messaging_product': "whatsapp",
            'to': phone_number,
            'type': "text",
            'text': {'body': message},
        }

        try:
            response = requests.post(f"{base_url}/messages", headers=headers, json=template_payload)
            response.raise_for_status()
            _logger.info("✅ WhatsApp text message sent successfully to %s", phone_number)
        except requests.exceptions.RequestException as e:
            _logger.error("❌ Failed to send WhatsApp message: %s", str(e))
            return False

        # --- Step 2: Generate PDF ---
        try:
            datas = self.print_job_card_invoice().get('data', {})
            pdf_content, _ = self.env['ir.actions.report'].sudo()._render_qweb_pdf(
                'machine_repair_management.print_job_card_invoice_template_document',
                [self.id],
                data=datas
            )
            _logger.info("📄 PDF generated successfully for invoice %s", self.invoice_no)
        except Exception as e:
            _logger.error("❌ Error rendering PDF for invoice %s: %s", self.invoice_no, str(e))
            raise ValidationError(f"Failed to generate PDF: {str(e)}")

        # --- Step 3: Upload and Send PDF ---
        file_name = f"{self.name}_{self.invoice_no}.pdf" if self.invoice_no else f"{self.name}.pdf"
        media_id = self._upload_pdf_meta(pdf_content, file_name)

        if not media_id:
            _logger.info("❌ Failed to upload PDF for %s", self.invoice_no)
            return False

        try:
            self.send_pdf_to_whatsapp(phone_number, media_id, file_name, self.name)
            _logger.info("✅ Invoice PDF sent successfully to WhatsApp for %s", phone_number)
        except Exception as e:
            _logger.error("❌ Failed to send PDF to WhatsApp: %s", str(e))
            return False

        return {
            'effect': {
                'type': 'rainbow_man',
                'fadeout': 'slow',
                'message': 'Your Invoice was sent successfully to the customer via WhatsApp.',
            }
        }

    '''  Working code Commented on Oct-15-2025 due to Invoice Add  Extra Message
    def send_whatsapp_invoice_receipt(self):
        if not self.whatsapp_send_bool:
            _logger.info("❌ No WhatsApp set in res Config Settings")
            return False
        phone_number = self.phone
        country_code = self.country_id.phone_code

        if not phone_number:
            _logger.info("❌ No Phone Number is linked")
            return
        phone_number = phone_number.replace('+', '').replace(' ', '')
        phone_number = f"{country_code}{phone_number}"

        whatsapp_opt_in = self.whatsapp_opt_in
        if not whatsapp_opt_in:
            _logger.info("❌ No WhatsApp opt-in for Customer %s", self.customer_name)
            return False
        pdf_content = False    
        try:

            datas = self.print_job_card_invoice().get('data', {})
            pdf_content, _ = self.env['ir.actions.report'].sudo()._render_qweb_pdf(
                'machine_repair_management.print_job_card_invoice_template_document',
                [self.id],
                data=datas
            )
            _logger.info("PDF generated for job card %s", self.name)
        except Exception as e:
            _logger.error("Error rendering PDF for job card %s: %s", self.name, str(e))
            raise ValidationError(f"Failed to generate PDF: {str(e)}")
        #     # pdf_content, _ = self.env['ir.actions.report'].sudo()._render_qweb_pdf('machine_repair_management.print_inspection_charge_receipt_template_document',[self.id])
        #     pdf_content, _ = self.env['ir.actions.report'].sudo()._render_qweb_pdf(
        #     'machine_repair_management.print_job_card_invoice_template_document', [self.id],
        #     data=self.print_job_card_invoice().get('data', {})
        #     )
        #     _logger.info("✅ PDF generated for Job order %s",self.name)
        #
        # except Exception as e: 
        #     _logger.info("Error rendering PDF for order %s: %s", self.name, str(e))

        file_name = f"Invoice {self.invoice_no}.pdf"
        media_id = self._upload_pdf_meta(pdf_content, file_name)
        if not media_id:
            _logger.info("❌ Failed to upload the media id %s", self.name)
            return

        self.send_pdf_to_whatsapp(phone_number, media_id, file_name, self.name)

        return {
            'effect':{
                'type': 'rainbow_man',
                'fadeout':'slow',
                'message': 'Your Invoice send Successfully to Customer Whatsapp Number',
                }
            }
    '''
    '''Whatsapp send for AC service unit Receipt report is added on August 1-2025'''

    '''This code is worked correctly for whatsapp unit pull out commented on oct 31 2025 due to  unit pull out arabic template and english template
    def _send_unit_receipt_whatsapp(self):
        if not self.whatsapp_send_bool:
            _logger.info("❌ No WhatsApp set in res Config Settings")
            return False
        phone_number = self.phone 
        country_code = self.country_id.phone_code
        phone_number = phone_number.replace('+', '').replace("", "")
        phone_number = f"{country_code}{phone_number}"

        whatsapp_opt_in = self.whatsapp_opt_in
        if not whatsapp_opt_in:
            _logger.info("❌ No WhatsApp opt-in for Customer %s", self.customer_name)
            return False
        pdf_content = False
        try:
            pdf_content, _ = self.env['ir.actions.report'].sudo()._render_qweb_pdf('machine_repair_management.ac_unit_service_receipt_document_hhs_report', [self.id])
            _logger.info("PDF generated for job card %s", self.name)

        except Exception as e:
            _logger.error("Error rendering PDF for job card %s: %s", self.name, str(e))
            raise ValidationError(f"Failed to generate PDF: {str(e)}")
        file_name = f"Ac Service Unit Receipt{self.name}.pdf"  
        media_id = self._upload_pdf_meta(pdf_content, file_name)
        if not media_id:
            _logger.info("❌ Failed to upload the media id %s", self.name)
            return 

        self.send_pdf_to_whatsapp(phone_number, media_id, file_name, self.name)
    '''

    def _send_unit_receipt_whatsapp(self):

        # if not self.whatsapp_send_bool:
        #     _logger.info("❌ No WhatsApp set in res Config Settings")
        #     return False
        if not self.env['ir.config_parameter'].sudo().get_param(
                'machine_repair_management.whatsapp_send_bool') == 'True':
            _logger.info("❌ No WhatsApp set in res Config Settings")
            return False

        whatsapp_opt_in = False
        whatsapp_opt = False
        message = False

        scheduled_state = self.env['project.task.type'].search(
            [('code', '=', '117')],
            limit=1
        )
        if scheduled_state:
            if scheduled_state.code == self.job_card_state_code:
                if scheduled_state.whatsapp_bool:
                    whatsapp_opt_in = True
                    arabic = scheduled_state.whatsapp_ar_template
                    english = scheduled_state.whatsapp_en_template
                    english_format = english.replace(
                        "{{customer name}}", self.customer_name or ''
                    ).replace("{{service number}}", self.name).replace("{{date}} ",
                                                                       self.planned_date_begin.strftime("%d-%m-%Y"))
                    arabic_format = arabic.replace("{{customer name}}", self.customer_name or '').replace(
                        "{{service number}}", self.name).replace("{{date}} ",
                                                                 self.planned_date_begin.strftime("%d-%m-%Y"))
                    separator = "\n" + "-" * 50 + "\n"
                    message = arabic_format + separator + english_format

        phone_number = self.phone
        # whatsapp_opt_in = self.whatsapp_opt_in
        country_code = self.country_id.phone_code
        if not whatsapp_opt_in:
            _logger.info("❌ No WhatsApp opt-in for customer for job card customer %s", self.customer_name)
            return False
        if not self.whatsapp_opt_in:
            _logger.info("❌ No WhatsApp opt-in for Customer %s", self.customer_name)
            return False

        if not phone_number:
            _logger.info("❌ No mobile number found for customer %s", self.customer_name)
            return False
        phone_number = phone_number.replace('+', ' ').replace('', '')
        phone_number = f"{country_code}{phone_number}"

        whatsapp_phone_number_id = f"{self.env['ir.config_parameter'].sudo().get_param('whatsapp_sale_order_notify.whatsapp_phone_number_id')}"

        base_url = f'https://graph.facebook.com/v18.0/{whatsapp_phone_number_id}'

        access_token = f"{self.env['ir.config_parameter'].sudo().get_param('whatsapp_sale_order_notify.whatsapp_access_token')}"

        if not access_token:
            _logger.error("❌ No WhatsApp access token configured")
            return False

        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'

        }
        template_url = f"{base_url}/messages"

        template_payload = {

            'messaging_product': "whatsapp",
            'to': phone_number,
            "type": "text",
            "text": {
                'body': message,
            }

        }
        # try:
        #     response = requests.post(template_url, headers=headers, json=template_payload)
        #     response.raise_for_status()  # Raise an exception for HTTP errors
        #
        #     # self.service_request_id.message_post(body=_("WhatsApp Job card %s Unit Pull Out sent successfully to the customer") % self.name)
        #     return True
        #
        # except requests.exceptions.RequestException as e:
        #     _logger.error("❌ WhatsApp message failed: %s", str(e))
        #
        #     return False

        try:
            response = requests.post(f"{base_url}/messages", headers=headers, json=template_payload)
            response.raise_for_status()
            _logger.info("✅ WhatsApp text message sent successfully to %s", phone_number)
        except requests.exceptions.RequestException as e:
            _logger.error("❌ Failed to send WhatsApp message: %s", str(e))
            return False

        try:

            pdf_content, _ = self.env['ir.actions.report'].sudo()._render_qweb_pdf(
                'machine_repair_management.ac_unit_service_receipt_document_hhs_report', [self.id])
            _logger.info("📄 PDF generated successfully for job card %s", self.name)
        except Exception as e:
            _logger.error("❌ Error rendering PDF for job card %s: %s", self.name, str(e))
            raise ValidationError(f"Failed to generate PDF: {str(e)}")

        # --- Step 3: Upload and Send PDF ---
        file_name = f"Ac Service Unit Receipt{self.name}.pdf"
        media_id = self._upload_pdf_meta(pdf_content, file_name)

        if not media_id:
            _logger.info("❌ Failed to upload PDF for %s", self.name)
            return False

        try:
            self.send_pdf_to_whatsapp(phone_number, media_id, file_name, self.name)
            _logger.info("✅ PDF sent successfully to WhatsApp for %s", phone_number)
        except Exception as e:
            _logger.error("❌ Failed to send PDF to WhatsApp: %s", str(e))
            return False

        return {
            'effect': {
                'type': 'rainbow_man',
                'fadeout': 'slow',
                'message': 'Unit Pull Out successfully to the customer via WhatsApp.',
            }
        }

    '''code added on Nov-11 due to ready to invoice  whatsapp to be sent'''

    def _send_whatsapp_job_card_report_for_ready_to_invoice(self):
        # if not self.whatsapp_send_bool:
        #     _logger.info("❌ No WhatsApp set in res Config Settings")
        #     return False
        if not self.env['ir.config_parameter'].sudo().get_param(
                'machine_repair_management.whatsapp_send_bool') == 'True':
            _logger.info("❌ No WhatsApp set in res Config Settings")
            return False

        phone_number = self.phone
        country_code = self.country_id.phone_code

        if not phone_number:
            _logger.info("❌ No Phone Number is linked")
            return False

        phone_number = phone_number.replace('+', '').replace(' ', '')
        phone_number = f"{country_code}{phone_number}"

        if not self.whatsapp_opt_in:
            _logger.info("❌ No WhatsApp opt-in for Customer %s", self.customer_name)
            return False

        whatsapp_phone_number_id = self.env['ir.config_parameter'].sudo().get_param(
            'whatsapp_sale_order_notify.whatsapp_phone_number_id')
        access_token = self.env['ir.config_parameter'].sudo().get_param(
            'whatsapp_sale_order_notify.whatsapp_access_token')

        if not access_token or not whatsapp_phone_number_id:
            _logger.error("❌ WhatsApp configuration missing")
            return False

        base_url = f'https://graph.facebook.com/v18.0/{whatsapp_phone_number_id}'
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }

        # --- Step 1: Send WhatsApp Text Message ---

        # message = (
        #     f"Dear {self.customer_name},\n\n"
        #     f"Please find attached your Proforma Invoice {self.name}.\n"
        #     f"Kindly review the details and proceed with the necessary actions.\n\n"
        #     f"HH-Shaker – Service Team"
        # )

        message = (
            f"عزيزي {self.customer_name}،\n"
            f"مرفق لكم الفاتورة المبدئية رقم {self.name}\n"
            "نرجو منكم مراجعة التفاصيل واتخاذ الإجراءات اللازمة.\n"
            "------------------------------------------------------\n"
            f"Dear {self.customer_name},\n"
            f"Please find attached the Service Job Card. {self.name}.\n"
            "Kindly review the details and take the necessary actions.\n"
            "HH-Shaker – Service Team"
        )

        template_payload = {
            'messaging_product': "whatsapp",
            'to': phone_number,
            'type': "text",
            'text': {'body': message},
        }

        try:
            response = requests.post(f"{base_url}/messages", headers=headers, json=template_payload)
            response.raise_for_status()
            _logger.info("✅ WhatsApp text message sent successfully to %s", phone_number)
        except requests.exceptions.RequestException as e:
            _logger.error("❌ Failed to send WhatsApp message: %s", str(e))
            return False

        # --- Step 2: Generate PDF ---
        try:
            datas = self.job_card_service_report().get('data', {})
            pdf_content, _ = self.env['ir.actions.report'].sudo()._render_qweb_pdf(
                'machine_repair_management.service_job_card_report',
                [self.id],
                data=datas
            )
            _logger.info("📄 Service Job Card Report PDF generated successfully for job card %s", self.name)
        except Exception as e:
            _logger.error("❌ Error rendering PDF for job card %s: %s", self.name, str(e))
            raise ValidationError(f"Failed to generate PDF: {str(e)}")

        # --- Step 3: Upload and Send PDF ---
        file_name = f"Service Job Card Report {self.name}.pdf"
        media_id = self._upload_pdf_meta(pdf_content, file_name)

        if not media_id:
            _logger.info("❌ Failed to upload PDF for %s", self.name)
            return False

        try:
            self.send_pdf_to_whatsapp(phone_number, media_id, file_name, self.name)
            _logger.info("✅ Service Job Card Report PDF sent successfully to WhatsApp for %s", phone_number)
        except Exception as e:
            _logger.error("❌ Failed to send PDF to WhatsApp: %s", str(e))
            return False

        return {
            'effect': {
                'type': 'rainbow_man',
                'fadeout': 'slow',
                'message': 'Your Service Job Card Report was sent successfully to the customer via WhatsApp.',
            }
        }

    '''Code is added on Nov 13 For sending Whatsapp Rescheduled -- 156'''

    def _send_whatsapp_for_rescheduled_with_parts(self):
        if not self.env['ir.config_parameter'].sudo().get_param(
                'machine_repair_management.whatsapp_send_bool') == 'True':
            _logger.info("❌ No WhatsApp set in res Config Settings")
            return False

        whatsapp_opt_in = False
        whatsapp_opt = False
        message = False

        scheduled_state = self.env['project.task.type'].search(
            [('code', '=', '134')],
            limit=1
        )

        slots = False
        english_slot = False
        arabic_slot = False

        if self.planned_date_begin:
            if (self.planned_date_begin.hour + 3) < 12:
                english_slot = f"{self.planned_date_begin.strftime('%d-%m-%Y')} in the Morning"
                arabic_slot = f"{self.planned_date_begin.strftime('%d-%m-%Y')}  في الفتره الصباحية"
                # slots = f"{self.planned_date_begin.strftime('%d-%m-%Y')} on morning :  الصباحيه (9:00 AM – 12:00 PM)"
            else:
                english_slot = f"{self.planned_date_begin.strftime('%d-%m-%Y')} in the Evening"
                arabic_slot = f"{self.planned_date_begin.strftime('%d-%m-%Y')}   في الفتره المسائيه"
                # slots = f"{self.planned_date_begin.strftime('%d-%m-%Y')} on Evening : المسائيه (1:00 PM – 5:00 PM)"

        if scheduled_state:
            if scheduled_state.code == self.job_card_state_code:
                if scheduled_state.whatsapp_bool:
                    whatsapp_opt = True
                    arabic = scheduled_state.whatsapp_ar_template
                    english = scheduled_state.whatsapp_en_template
                    english_format = english.replace(
                        "{{customer name}}", self.customer_name or ''
                    ).replace("{{Service request No}}", str(self.name)).replace("{{date}}", english_slot).replace(
                        "{{technician name}}", self.team_id.name)
                    arabic_format = arabic.replace("{{customer name}}", self.customer_name or '').replace(
                        "{{Service request No}}", str(self.name)).replace("{{date}}", arabic_slot).replace(
                        "{{technician name}}", self.team_id.name)
                    separator = "\n" + "-" * 50 + "\n"
                    message = arabic_format + separator + english_format

        phone_number = self.phone

        whatsapp_opt_in = self.whatsapp_opt_in
        country_code = self.country_id.phone_code
        if not whatsapp_opt:
            _logger.info("❌ No WhatsApp opt-in Project Task Stages %s", self.customer_name)
            return False

        if not whatsapp_opt_in:
            _logger.info("❌ No WhatsApp opt-in for customer for job card customer %s", self.customer_name)
            return False
        if not phone_number:
            _logger.info("❌ No mobile number found for customer %s", self.customer_name)
            return False
        phone_number = phone_number.replace('+', ' ').replace('', '')
        phone_number = f"{country_code}{phone_number}"

        whatsapp_phone_number_id = f"{self.env['ir.config_parameter'].sudo().get_param('whatsapp_sale_order_notify.whatsapp_phone_number_id')}"

        base_url = f'https://graph.facebook.com/v18.0/{whatsapp_phone_number_id}'

        access_token = f"{self.env['ir.config_parameter'].sudo().get_param('whatsapp_sale_order_notify.whatsapp_access_token')}"

        if not access_token:
            _logger.error("❌ No WhatsApp access token configured")
            return False
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'

        }
        template_url = f"{base_url}/messages"

        template_payload = {

            'messaging_product': "whatsapp",
            'to': phone_number,
            "type": "text",
            "text": {
                'body': message,
            }

        }
        try:
            response = requests.post(template_url, headers=headers, json=template_payload)
            response.raise_for_status()  # Raise an exception for HTTP errors

            # Use message_notify instead of message_post for user notifications
            self.service_request_id.message_post(body=_(
                "WhatsApp Job card %s Re-scheduled message With Parts sent successfully to the customer") % self.name)
            return True

        except requests.exceptions.RequestException as e:
            _logger.error("❌ WhatsApp message failed: %s", str(e))
            # Optionally, notify the user or log the error in the chatter
            self.service_request_id.message_post(
                body=_("WhatsApp Re-scheduled with Parts message sent successfully to %s") % self.partner_id.name,
                message_type='notification',

            )
            return False

    ''' Code is added on Nov 11 -2025 for cancellation reason send to customer whatsapp'''

    def _send_whatsapp_for_cancellation(self):
        # if not self.whatsapp_send_bool:
        #     _logger.info("❌ No WhatsApp set in res Config Settings")
        #     return False
        if not self.env['ir.config_parameter'].sudo().get_param(
                'machine_repair_management.whatsapp_send_bool') == 'True':
            _logger.info("❌ No WhatsApp set in res Config Settings")
            return False

        whatsapp_opt_in = False
        message = False

        scheduled_state = self.env['project.task.type'].search(
            [('code', '=', '124')],
            limit=1
        )
        if scheduled_state:
            if scheduled_state.code == self.job_card_state_code:
                if scheduled_state.whatsapp_bool:
                    if self.cancellation_reason_id.name.lower() == 'customer no response':
                        whatsapp_opt_in = True
                        arabic = scheduled_state.whatsapp_ar_template
                        english = scheduled_state.whatsapp_en_template
                        english = english.replace("Dear Customer", f"Dear {self.customer_name}").replace("Midea",
                                                                                                         self.product_category_id.name)
                        arabic = arabic.replace("{{customer name}}", f"{self.customer_name}")
                        separator = "\n" + "-" * 50 + "\n"
                        message = arabic + separator + english
                    else:

                        whatsapp_opt_in = True

                        message = (

                            f"عزيزي {self.customer_name},\n"
                            f"تم إلغاء موعدكم المحدد بسبب *{self.cancellation_reason_id.arabic_name}*.  \n"
                            f"يرجى التواصل مع خدمة العملاء على الرقم 8002440247 لإعادة جدولة الموعد في الوقت المناسب لكم. \n"
                            f"شكراً لتعاونكم.\n"
                            "---------------------------------------------------------\n"
                            f"Dear {self.customer_name},\n"
                            f"Your scheduled appointment has been cancelled due to *{self.cancellation_reason_id.name or ''}*.\n"
                            f"Please call our Customer Service at 8002440247 to reschedule your appointment.\n"
                            f"Thank you for your cooperation.\n"
                            "HH-Shaker – Service Team"
                        )

                        # whatsapp_opt_in = True
                        # arabic = scheduled_state.whatsapp_ar_template
                        # english = scheduled_state.whatsapp_en_template
                        # english = english.replace("Dear Customer",f"Dear {self.customer_name}").replace("Midea",self.product_category_id.name)
                        # separator = "\n" + "-" * 50 + "\n"
                        # message = arabic + separator + english
                        #

        phone_number = self.phone
        # whatsapp_opt_in = self.whatsapp_opt_in
        country_code = self.country_id.phone_code
        if not whatsapp_opt_in:
            _logger.info("❌ No WhatsApp opt-in for customer for job card customer %s", self.customer_name)
            return False
        if not self.whatsapp_opt_in:
            _logger.info("❌ No WhatsApp opt-in for Customer %s", self.customer_name)
            return False
        if not phone_number:
            _logger.info("❌ No mobile number found for customer %s", self.customer_name)
            return False
        phone_number = phone_number.replace('+', ' ').replace('', '')
        phone_number = f"{country_code}{phone_number}"

        whatsapp_phone_number_id = f"{self.env['ir.config_parameter'].sudo().get_param('whatsapp_sale_order_notify.whatsapp_phone_number_id')}"

        base_url = f'https://graph.facebook.com/v18.0/{whatsapp_phone_number_id}'

        access_token = f"{self.env['ir.config_parameter'].sudo().get_param('whatsapp_sale_order_notify.whatsapp_access_token')}"

        if not access_token:
            _logger.error("❌ No WhatsApp access token configured")
            return False
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'

        }
        template_url = f"{base_url}/messages"

        template_payload = {

            'messaging_product': "whatsapp",
            'to': phone_number,
            "type": "text",
            "text": {
                'body': message,
            }

        }
        try:
            response = requests.post(template_url, headers=headers, json=template_payload)
            response.raise_for_status()  # Raise an exception for HTTP errors

            self.service_request_id.message_post(body=_(
                "WhatsApp Job card %s Failed to attend call message sent successfully to the customer") % self.name)
            return True

        except requests.exceptions.RequestException as e:
            _logger.error("❌ WhatsApp message failed: %s", str(e))
            # Optionally, notify the user or log the error in the chatter
            self.service_request_id.message_post(
                body=_("WhatsApp Failed message sent successfully to %s") % self.partner_id.name,
                message_type='notification',

            )
            return False

    ''' This is also worked for time being commented by Vijaya bhaskar on June 12 2025 for  all whatsapp in one function
    def send_whatsapp_inspection_receipt(self):
        # Validate phone number
        if not self.phone:
            _logger.error("❌ No Phone Number is linked for task %s", self.name)
            return False

        phone_number = self.phone.replace('+', '').replace(' ', '')

        try:
            # Determine which report to generate based on conditions
            file_name = False
            report_name = False
            if self.service_charge_receipt_print_click:
                report_name = 'machine_repair_management.print_job_card_receipt_template_document'
                file_name = f"Service Charges Receipt {self.name}.pdf"
            elif self.invoice_receipt_print_click:
                report_name = 'machine_repair_management.print_job_card_invoice_template_document'
                file_name = f"Invoice Receipt {self.invoice_no}.pdf"
            elif self.inspection_charges_receipt_click:  
                report_name = 'machine_repair_management.print_inspection_charge_receipt_template_document'
                file_name = f"Inspection Charges Receipt {self.name}.pdf"

            # Generate PDF
            pdf_content, _ = self.env['ir.actions.report'].sudo()._render_qweb_pdf(
                report_name, 
                [self.id],
                data=self.print_inspection_charge_receipt().get('data', {})
            )

            _logger.info("✅ PDF generated for Job order %s", self.name)

            # Upload to WhatsApp
            media_id = self._upload_pdf_meta(pdf_content, file_name)
            if not media_id:
                _logger.error("❌ Failed to upload PDF for task %s", self.name)
                return False

            # Send via WhatsApp
            return self.send_pdf_to_whatsapp(phone_number, media_id, file_name, self.name)

        except Exception as e:
            _logger.error("❌ Error sending WhatsApp receipt for task %s: %s", self.name, str(e))
            return False    
        '''

    def _upload_pdf_meta(self, pdf_content, file_name):
        if not self.whatsapp_send_bool:
            _logger.info("❌ No WhatsApp set in res Config Settings")
            return False

        # url = 'https://graph.facebook.com/v18.0/629139543620025/media'
        whatsapp_phone_number_id = f"{self.env['ir.config_parameter'].sudo().get_param('whatsapp_sale_order_notify.whatsapp_phone_number_id')}"
        url = f'https://graph.facebook.com/v18.0/{whatsapp_phone_number_id}/media'

        access_token = f"{self.env['ir.config_parameter'].sudo().get_param('whatsapp_sale_order_notify.whatsapp_access_token')}"

        headers = {
            'Authorization': f'Bearer {access_token}',
        }

        files = {
            'file': (file_name, pdf_content, 'application/pdf'),
            'type': (None, 'document'),
            'messaging_product': (None, 'whatsapp')
        }

        try:
            response = requests.post(url, headers=headers, files=files)
            response.raise_for_status()
            media_id = response.json().get('id')
            _logger.info("✅ Uploaded PDF to WhatsApp. Media ID: %s", media_id)
            return media_id

        except requests.exceptions.RequestException as e:
            _logger.error("❌ Media upload failed: %s", str(e))
            return None

    def send_pdf_to_whatsapp(self, phone_number, media_id, file_name, order_name):
        # base_url = 'https://graph.facebook.com/v18.0/629139543620025'  # Your phone number ID

        if not self.whatsapp_send_bool:
            _logger.info("❌ No WhatsApp set in res Config Settings")
            return False
        whatsapp_phone_number_id = f"{self.env['ir.config_parameter'].sudo().get_param('whatsapp_sale_order_notify.whatsapp_phone_number_id')}"
        base_url = f'https://graph.facebook.com/v18.0/{whatsapp_phone_number_id}'  # Your phone number ID

        access_token = f"{self.env['ir.config_parameter'].sudo().get_param('whatsapp_sale_order_notify.whatsapp_access_token')}"

        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }

        # 1. First send the document
        document_url = f"{base_url}/messages"
        document_payload = {
            'messaging_product': 'whatsapp',
            'recipient_type': 'individual',
            'to': phone_number,
            'type': 'document',
            'document': {
                'id': media_id,
                'filename': file_name,
                'caption': f'{order_name}'
            }
        }

        try:
            response = requests.post(document_url, headers=headers, json=document_payload)
            response.raise_for_status()
            _logger.info("✅ Sent WhatsApp PDF to %s for order %s", phone_number, order_name)
        except requests.exceptions.RequestException as e:
            _logger.error("❌ WhatsApp document send error: %s", str(e))
            # Don't return here, try to send the template anyway

        # 2. Then send the template message
        template_url = f"{base_url}/messages"

        # template_payload = {
        #     'messaging_product': 'whatsapp',
        #     'recipient_type': 'individual',
        #     'to': phone_number,
        #     'type': 'template',
        #     'template': {
        #         'name': 'welcome_message',
        #         'language': {
        #             'code': 'en'
        #         },
        #         'components': [
        #             {
        #                 'type': 'body',
        #                 'parameters': [
        #                     {
        #                         'type': 'text',
        #                         'text': order_name
        #                     }
        #                 ]
        #             }
        #         ]
        #     }
        # }
        # ## working
        template_payload = {
            'messaging_product': 'whatsapp',
            'recipient_type': 'individual',
            'to': phone_number,
            'type': 'template',
            'template': {
                'name': 'simple_greeting',
                'language': {
                    'code': 'en'
                },
                'components': [
                    {
                        'type': 'body',
                        'parameters': [
                            {
                                'type': 'text',
                                'text': order_name
                            }
                        ]
                    }
                ]
            }
        }

        try:
            response_template = requests.post(template_url, headers=headers, json=template_payload)
            response_template.raise_for_status()
            _logger.info("✅ Sent WhatsApp template to %s for order %s", phone_number, order_name)
        except requests.exceptions.RequestException as e:
            _logger.error("❌ WhatsApp template send error: %s", str(e))

    '''Code added on Nov 19 2025'''



class SymptomsLine(models.Model):
    _name = 'project.task.symptoms'

    code = fields.Many2one('symptoms', string="Symptoms")
    project_task_id = fields.Many2one('project.task', string="Symptoms Line")

    # description = fields.Char(string="Description")

    # @api.onchange('code')
    # def _description_name_onchange(self):
    #     for rec in self:
    #         if rec.code:
    #             # rec.description = f"{rec.symptoms_type_id.sym_code} - {rec.symptoms_type_id.sym_desc}"
    #             rec.description = rec.code.sym_desc

    ''' This code is commented by Vijaya Bhaskar on June -12-2025 for asking validation '''

    @api.constrains('code', 'project_task_id')
    def _check_duplicate_symptom(self):
        for rec in self:
            # Check if this symptom is already associated with the current job card (symptoms_id)
            if rec.code and rec.project_task_id:
                existing_symptom = self.env['project.task.symptoms'].search([
                    ('project_task_id', '=', rec.project_task_id.id),
                    ('code', '=', rec.code.id),
                    ('id', '!=', rec.id)
                ], limit=1)
                if existing_symptom:
                    raise ValidationError(
                        "This symptom has already been added to the Symptoms Line for this job card."
                    )

    @api.onchange('code')
    def _onchange_code(self):
        for rec in self:
            if rec.project_task_id and rec.project_task_id.service_request_id:
                service_request = rec.project_task_id.service_request_id
                service_request.symptom_line_ids = [(5, 0, 0)]
                lines_to_add = []
                for symptom_line in rec.project_task_id.symptoms_line_ids:
                    if symptom_line.code:
                        vals = {
                            'sym_id': symptom_line.code.id,
                        }
                        lines_to_add.append((0, 0, vals))

                service_request.symptom_line_ids = lines_to_add

    # @api.onchange('code')
    # def _onchange_code(self):
    #     for rec in self:
    #         if rec.project_task_id and rec.project_task_id.service_request_id:
    #             # Clear existing symptom lines
    #             # rec.project_task_id.service_request_id.symptom_line_ids = [(5, 0, 0)]
    #
    #             # Add new symptom lines based on selected codes
    #             lines = []
    #             for line in rec.code:
    #                 vals = {
    #                     'sym_id': line.id,
    #                 }
    #                 lines.append((0, 0, vals))
    #
    #             rec.project_task_id.service_request_id.symptom_line_ids = lines
    #


class DefectsLine(models.Model):
    _name = 'project.task.defects'

    code = fields.Many2one('defects', string="Defects")
    # description = fields.Char(string="Description")
    project_task_id = fields.Many2one('project.task', string="Defects Line")

    # @api.onchange('code')
    # def _defects_description_name_onchange(self):
    #     for rec in self:
    #         if rec.code:
    #             # rec.code_desc = f"{rec.defects_type_id.def_code} - {rec.defects_type_id.def_desc}"
    #             rec.description = rec.code.def_desc

    ''' This code is commented by Vijaya bhaskar on June -12-2025 for asking validation '''

    @api.constrains('code', 'project_task_id')
    def _check_duplicate_defects(self):
        for rec in self:
            # Check if this defects is already associated with the current job card (defects_id)
            existing_defects = self.search([
                ('project_task_id', '=', rec.project_task_id.id),
                ('code', '=', rec.code.id),
                ('id', '!=', rec.id)
            ])
            if existing_defects:
                raise ValidationError(
                    "This defects has already been added to the Defects Line for this job card."
                )


class serviceLine(models.Model):
    _name = 'project.task.service'

    code = fields.Many2one('repair.type', string="Services")

    # description = fields.Char(string='Description')
    # action_type = fields.Selection(
    #     [('preventive', 'Preventive'), ('corrective', 'Corrective')],
    #     string='Type', required=True, default='preventive')
    under_warranty = fields.Boolean(string='UW', default=False)
    project_task_id = fields.Many2one('project.task', string="Service Line")

    ''' This code is commented by Vijaya bhaskar on June -12-2025 for asking validation  '''

    @api.constrains('code', 'project_task_id')
    def _check_duplicate_service(self):
        for rec in self:
            # Check if this service is already associated with the current job card (defects_id)

            existing_service = self.search([
                ('project_task_id', '=', rec.project_task_id.id),
                ('code', '=', rec.code.id),
                ('id', '!=', rec.id)
            ])
            if existing_service:
                raise ValidationError(
                    "This service has already been added to the Service Line for this job card."
                )

    @api.onchange('code')
    def _onchange_code(self):
        for rec in self:
            if rec.code:
                if rec.project_task_id.warranty:
                    rec.under_warranty = rec.project_task_id.warranty

    # @api.onchange('code')
    # def _service_description_name_onchange(self):
    #     for rec in self:
    #         if rec.code:
    #             # rec.code_desc = f"{rec.service_type_id.code} - {rec.service_type_id.name}"
    #             rec.description = rec.code.name


class ProductLine(models.Model):
    _name = 'product.lines'
    _description = 'Product Consume Part/Service'

    # product_id = fields.Many2one('product.product', string='Product', required=True,
    #     domain=lambda self: self._get_product_domain()
    #     )

    # product_id = fields.Many2one('product.product', string='Product', required=True,
    #                             domain="[('is_machine', '=', False)]")

    product_id = fields.Many2one(
        'product.product',
        string='Product',
    )
    qty = fields.Float(string='Qty', required=True, default=1.0)
    uom_id = fields.Many2one('uom.uom', string='UOM', readonly=True)
    price_unit = fields.Float(string='Unit Price', required=True)
    vat = fields.Float(string='VAT (%)', required=True, default=0.0, readonly=True)
    total = fields.Float(string='Total', compute='_compute_total', store=True)
    project_task_id = fields.Many2one('project.task', string="Product Lines", readonly=True,
                                      default=lambda self: self.env.context.get('default_project_task_id', False))

    under_warranty_bool = fields.Boolean(string="UW", default=False, )

    tax_amount = fields.Float(string="Tax Amount")
    ''' for report purpose they want this field'''
    standard_price = fields.Float(string="Standard Price")

    product_categ_id = fields.Many2one('product.category', string="Product Category",
                                       related="project_task_id.product_category_id")

    product_ids = fields.Many2many('product.product', string='Product filter')

    # product_ids = fields.Many2many('product.product', string='Product filter',compute = "_compute_product_ids", store=True)

    parts_reserved_bool = fields.Boolean(string='Parts Reserved', default=False)

    # parts_bool = fields.Boolean()

    parts_reserved_qty = fields.Float(string="Parts Reserved Qty", store=True, compute="_compute_parts_reserved_qty")

    on_hand_qty = fields.Float(string="O/H hand Qty")

    warehouse_id = fields.Many2one('stock.warehouse', related="project_task_id.warehouse_id", store=True)

    location_id = fields.Many2one('stock.location', string="Stock Location", compute="_compute_location_id",
                                  store=False)

    overall_qty = fields.Float(string="Branch QTY")

    amc_project_bool = fields.Boolean("AMC Project", default=False)

    # @api.depends('project_task_id.amc_project_id')
    # def _compute_amc_project_bool(self):
    #     for rec in self:
    #         print("Raj", rec.project_task_id.amc_project_id)
    #         rec.amc_project_bool = False
    #         if rec.project_task_id.amc_project_id:
    #             rec.amc_project_bool = True

    # @api.depends('location_id', 'project_task_id')
    # def _compute_product_id_domain(self):
    #     """Compute the domain for product_id based on location and user groups."""
    #     for record in self:
    #         product_ids = record._get_product_domain()
    #         record.product_id_domain = product_ids or []
    #
    # def _get_product_domain(self):
    #     """Return list of product IDs with available stock in the specified location."""
    #     # Get location: priority to self.location_id, fallback to project's location_id
    #     location = False
    #     if self.location_id:
    #         location = self.location_id
    #     elif self.project_task_id and hasattr(self.project_task_id, 'location_id') and self.project_task_id.location_id:
    #         location = self.project_task_id.location_id
    #
    #     # Log location for debugging
    #     _logger.info("Location used for product_id domain: %s (ID: %s)",
    #                  location.name if location else "None", location.id if location else None)
    #
    #     # Initialize product_ids
    #     product_ids = []
    #     if location:
    #         # Get products with available stock in the specified location
    #         quants = self.env['stock.quant'].search([
    #             ('location_id', '=', location.id),
    #             ('quantity', '>', 0),
    #             ('product_id.active', '=', True),
    #         ])
    #         product_ids = quants.mapped('product_id').ids
    #         _logger.info("Products found in location %s: %s (Count: %s)",
    #                      location.name, product_ids, len(product_ids))
    #
    #     # Filter by user group and service/parts type
    #     if self.env.user.has_group('machine_repair_management.group_technical_allocation_user'):
    #         supervisor_service = self.env['ir.config_parameter'].sudo().get_param(
    #             'machine_repair_management.supervisor_service_product_add') == 'True'
    #         supervisor_parts = self.env['ir.config_parameter'].sudo().get_param(
    #             'machine_repair_management.supervisor_parts_product_add') == 'True'
    #
    #         if supervisor_service and not supervisor_parts:
    #             product_ids = self.env['product.product'].search([
    #                 ('id', 'in', product_ids),
    #                 ('service_type_bool', '=', True),
    #                 ('active', '=', True)
    #             ]).ids
    #         elif supervisor_parts and not supervisor_service:
    #             product_ids = self.env['product.product'].search([
    #                 ('id', 'in', product_ids),
    #                 ('service_type_bool', '=', False),
    #                 ('active', '=', True)
    #             ]).ids
    #         elif not supervisor_service and not supervisor_parts:
    #             product_ids = []
    #             _logger.info("No service or parts allowed, no products returned")
    #
    #     if not product_ids:
    #         _logger.info("No products available for location %s or user group restrictions",
    #                      location.name if location else "None")
    #
    #     return product_ids

    product_id_domain = fields.Char(
        string="Product ID Domain",
        compute="_compute_product_id_domain",
        readonly=True,
        store=False
    )

    @api.depends('project_task_id', 'location_id')
    def _compute_product_id_domain(self):
        """Compute the domain for product_id based on location and user groups."""
        for rec in self:
            rec.product_id_domain = False
            if rec.product_categ_id and not rec.project_task_id.project_related_amc_bool:
                products = self.env['product.product'].search([('categ_id', 'child_of', rec.product_categ_id.id)])
                location = rec.location_id or (
                    rec.project_task_id.location_id
                    if rec.project_task_id and hasattr(rec.project_task_id, 'location_id')
                    else False
                )

                if location:
                    quants = self.env['stock.quant'].search([
                        ('location_id', '=', location.id),
                        ('product_id.active', '=', True),
                    ])
                    products = quants.mapped('product_id')

                # Filter by user group and service/parts type
                if rec.env.user.has_group('machine_repair_management.group_technical_allocation_user'):
                    supervisor_service = rec.env['ir.config_parameter'].sudo().get_param(
                        'machine_repair_management.supervisor_service_product_add') == 'True'
                    supervisor_parts = rec.env['ir.config_parameter'].sudo().get_param(
                        'machine_repair_management.supervisor_parts_product_add') == 'True'

                    if supervisor_service and not supervisor_parts:
                        products = products.filtered(lambda p: p.service_type_bool)
                    elif supervisor_parts and not supervisor_service:
                        products = products.filtered(lambda p: not p.service_type_bool)

                elif rec.env.user.has_group('machine_repair_management.group_parts_user'):
                    parts_service = rec.env['ir.config_parameter'].sudo().get_param(
                        'machine_repair_management.parts_service_product_add') == 'True'
                    parts_parts = rec.env['ir.config_parameter'].sudo().get_param(
                        'machine_repair_management.parts_user_parts_product_add') == 'True'

                    if parts_service and not parts_parts:
                        products = products.filtered(lambda p: p.service_type_bool)
                    elif parts_parts and not parts_service:
                        products = products.filtered(lambda p: not p.service_type_bool)

                elif rec.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
                    tech_service = rec.env['ir.config_parameter'].sudo().get_param(
                        'machine_repair_management.technician_service_product_add') == 'True'
                    tech_parts = rec.env['ir.config_parameter'].sudo().get_param(
                        'machine_repair_management.technician_parts_product_add') == 'True'

                    if tech_service and not tech_parts:
                        products = products.filtered(lambda p: p.service_type_bool)
                    elif tech_parts and not tech_service:
                        products = products.filtered(lambda p: not p.service_type_bool)

                if not products:

                    rec.product_id_domain = "[('id', 'in', [])]"
                else:
                    rec.product_id_domain = "[('id', 'in', %s)]" % products.ids
            else:
                if rec.project_task_id.project_related_amc_bool:
                    print("Raj >>>>>>>>>>>><<<<<<<<<<<<<<<< 11", rec.location_id.id, rec.location_id.name)
                    products = self.env['product.product'].search(
                        [('stock_quant_ids.location_id', '=', rec.location_id.id)])
                    print("PRODUCT COUNT >>>>>>>>>>>>>", len(products))

                    location = rec.location_id or (
                        rec.project_task_id.location_id
                        if rec.project_task_id and hasattr(rec.project_task_id, 'location_id')
                        else False
                    )

                    if location:
                        quants = self.env['stock.quant'].search([
                            ('location_id', '=', location.id),
                            ('product_id.active', '=', True),
                        ])
                        products = quants.mapped('product_id')

                    # Filter by user group and service/parts type
                    if rec.env.user.has_group('machine_repair_management.group_technical_allocation_user'):
                        supervisor_service = rec.env['ir.config_parameter'].sudo().get_param(
                            'machine_repair_management.supervisor_service_product_add') == 'True'
                        supervisor_parts = rec.env['ir.config_parameter'].sudo().get_param(
                            'machine_repair_management.supervisor_parts_product_add') == 'True'

                        if supervisor_service and not supervisor_parts:
                            products = products.filtered(lambda p: p.service_type_bool)
                        elif supervisor_parts and not supervisor_service:
                            products = products.filtered(lambda p: not p.service_type_bool)

                    elif rec.env.user.has_group('machine_repair_management.group_parts_user'):
                        parts_service = rec.env['ir.config_parameter'].sudo().get_param(
                            'machine_repair_management.parts_service_product_add') == 'True'
                        parts_parts = rec.env['ir.config_parameter'].sudo().get_param(
                            'machine_repair_management.parts_user_parts_product_add') == 'True'

                        if parts_service and not parts_parts:
                            products = products.filtered(lambda p: p.service_type_bool)
                        elif parts_parts and not parts_service:
                            products = products.filtered(lambda p: not p.service_type_bool)

                    elif rec.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
                        tech_service = rec.env['ir.config_parameter'].sudo().get_param(
                            'machine_repair_management.technician_service_product_add') == 'True'
                        tech_parts = rec.env['ir.config_parameter'].sudo().get_param(
                            'machine_repair_management.technician_parts_product_add') == 'True'

                        if tech_service and not tech_parts:
                            products = products.filtered(lambda p: p.service_type_bool)
                        elif tech_parts and not tech_service:
                            products = products.filtered(lambda p: not p.service_type_bool)

                    if not products:

                        rec.product_id_domain = "[('id', 'in', [])]"
                    else:
                        rec.product_id_domain = "[('id', 'in', %s)]" % products.ids

    # @api.depends('location_id', 'project_task_id')
    # def _compute_product_id_domain(self):
    #     """Compute the domain for product_id based on location and user groups."""
    #     for record in self:
    #         product_ids = record._get_product_domain()
    #         record.product_id_domain = product_ids or []
    # @api.depends('location_id', 'project_task_id')
    # def _compute_product_id_domain(self):
    #     """Return list of product IDs with available stock in the specified location."""
    #     self.ensure_one()
    #     location = self.location_id
    #
    #     _logger.info(".........................Location used for product_id domain: %s (ID: %s)",
    #                  location.name if location else "None", location.id if location else None)
    #
    #     product_ids = []
    #     if location:
    #         quants = self.env['stock.quant'].search([
    #             ('location_id', '=', location.id),
    #             ('quantity', '>', 0),
    #             ('product_id.active', '=', True),
    #         ])
    #         product_ids = quants.mapped('product_id').ids
    #         _logger.info("Products found in location %s: %s (Count: %s)",
    #                      location.name, product_ids, len(product_ids))
    #
    #     if self.env.user.has_group('machine_repair_management.group_technical_allocation_user'):
    #         supervisor_service = self.env['ir.config_parameter'].sudo().get_param(
    #             'machine_repair_management.supervisor_service_product_add') == 'True'
    #         supervisor_parts = self.env['ir.config_parameter'].sudo().get_param(
    #             'machine_repair_management.supervisor_parts_product_add') == 'True'
    #
    #         if supervisor_service and not supervisor_parts:
    #             product_ids = self.env['product.product'].search([
    #                 ('id', 'in', product_ids),
    #                 ('service_type_bool', '=', True),
    #                 ('active', '=', True)
    #             ]).ids
    #         elif supervisor_parts and not supervisor_service:
    #             product_ids = self.env['product.product'].search([
    #                 ('id', 'in', product_ids),
    #                 ('service_type_bool', '=', False),
    #                 ('active', '=', True)
    #             ]).ids
    #         elif not supervisor_service and not supervisor_parts:
    #             product_ids = []
    #             _logger.info("No service or parts allowed, no products returned")
    #
    #     # if not product_ids:
    #     #     _logger.info("No products available for location %s or user group restrictions",
    #     #                  location.name if location else "None")
    #     print("/......................maaaaaaa..",product_ids)
    #     #
    #     return product_ids

    # @api.onchange('location_id', 'project_task_id')
    # def _onchange_location(self):
    #     """Dynamically update product_id domain based on the selected location or project task."""
    #     return {'domain': {'product_id': self._get_product_domain()}}

    # def _get_product_domain(self):
    #     """Dynamic domain for product_id based on user groups and location with available stock."""
    #     domain = []
    #
    #     # Get location: priority to self.location_id, fallback to project's location_id
    #     location = False
    #     if self.location_id:
    #         location = self.location_id
    #     elif self.project_task_id and hasattr(self.project_task_id, 'location_id') and self.project_task_id.location_id:
    #         location = self.project_task_id.location_id
    #
    #     # Filter by user group and service/parts type
    #     if self.env.user.has_group('machine_repair_management.group_technical_allocation_user'):
    #         supervisor_service = self.env['ir.config_parameter'].sudo().get_param(
    #             'machine_repair_management.supervisor_service_product_add') == 'True'
    #         supervisor_parts = self.env['ir.config_parameter'].sudo().get_param(
    #             'machine_repair_management.supervisor_parts_product_add') == 'True'
    #
    #         if supervisor_service and not supervisor_parts:
    #             domain.append(('service_type_bool', '=', True))
    #         elif supervisor_parts and not supervisor_service:
    #             domain.append(('service_type_bool', '=', False))
    #         elif not supervisor_service and not supervisor_parts:
    #             # If neither is selected, show no products
    #             domain.append(('id', '=', 0))
    #
    #     if location:
    #         # Get products with available stock in the specified location
    #         quants = self.env['stock.quant'].search([
    #             ('location_id', '=', location.id),
    #             ('quantity', '>', 0),
    #
    #         ])
    #         product_ids = quants.mapped('product_id').ids
    #         if product_ids:
    #             domain.append(('id', 'in', product_ids))
    #             print("....................lend",len(product_ids))
    #         else:
    #             # If no products have stock, show no products
    #             domain.append(('id', '=', 0))
    #     print("..................location",location)
    #     print(".....................................domia",domain)
    #     return domain
    # @api.onchange('location_id')
    # def _onchange_location(self):
    #     """Dynamically filter product_id based on the selected location."""
    #     if self.location_id:
    #         self._get_product_domain()
    #         # return {
    #         #     'domain': {
    #         #         'product_id': [('stock_quant_ids.location_id', '=', self.location_id.id)]
    #         #     }
    #         # }

    # @api.onchange('location_id')
    # def _onchange_location(self):
    #     """Dynamically filter product_id based on the selected location."""
    #     if self.location_id:
    #         return {
    #             'domain': {
    #                 'product_id': [('stock_quant_ids.location_id', '=', self.location_id.id)]
    #             }
    #         }
    # supervisor_service_product_add_line = fields.Boolean(string = "Supervisor Add Service Record", help = "Supervisor user add the service product/Not",
    #                                                 related = "project_task_id.supervisor_service_product_add",deprecated = False)
    #
    #
    # technician_service_product_add_line = fields.Boolean(string = "Technician Add Service Record",help = "Technician user add the service Product/Not",
    #                                                  related = "project_task_id.technician_service_product_add",deprecated = False)
    #
    # parts_service_product_add_line = fields.Boolean(string = "Parts Add Service Record", help = "Parts User add the service Record/Not",
    #                                                  related = "project_task_id.parts_service_product_add",deprecated = False)
    #
    # supervisor_parts_product_add_line = fields.Boolean(string = "Supervisor Add Parts Record", help = "Supervisor User add the Parts Product/Not",
    #                                               related = "project_task_id.supervisor_parts_product_add",deprecated = False)
    #
    #
    # technician_parts_product_add_line = fields.Boolean(string = "Technician Add Service Record",  help = "Technician user add the Parts Product/Not",
    #                                                 related = "project_task_id.technician_parts_product_add",deprecated = False)
    #
    # parts_user_parts_product_add_line = fields.Boolean(string = "Parts Add Parts Record", help = "Parts User add the Parts Record/Not",
    #                                             related = "project_task_id.parts_user_parts_product_add",deprecated = False)

    # def _get_product_domain(self):
    #     """Dynamic domain for product_id based on user groups and location"""
    #     domain = []
    #
    #     location = False
    #     # Get location: priority to self.location_id, fallback to project's location_id
    #     if hasattr(self, 'location_id') and self.location_id:
    #         location = self.location_id
    #
    #     elif hasattr(self, 'project_task_id') and self.project_task_id and getattr(self.project_task_id, 'location_id', False):
    #         location = self.project_task_id.location_id
    #     print(".......................location",location,self.project_task_id)
    #     # Filter by user group and service/parts type
    #     if self.env.user.has_group('machine_repair_management.group_technical_allocation_user'):
    #         supervisor_service = self.env['ir.config_parameter'].sudo().get_param('machine_repair_management.supervisor_service_product_add') == 'True'
    #         supervisor_parts = self.env['ir.config_parameter'].sudo().get_param('machine_repair_management.supervisor_parts_product_add') == 'True'
    #
    #         if supervisor_service and not supervisor_parts:
    #             domain.append(('service_type_bool', '=', True))
    #             print("...............indisssssssssss",domain)
    #         elif supervisor_parts and not supervisor_service:
    #             domain.append(('service_type_bool', '=', False))
    #         elif not supervisor_service and not supervisor_parts:
    #             # If neither is selected, show no products
    #             domain.append(('id', '=', 0))
    #         # If both are selected, show all products (no additional filter)
    #
    #     # elif self.env.user.has_group('machine_repair_management.group_parts_user'):
    #     #     parts_service = self.env['ir.config_parameter'].sudo().get_param('machine_repair_management.parts_service_product_add') == 'True'
    #     #     parts_parts = self.env['ir.config_parameter'].sudo().get_param('machine_repair_management.parts_user_parts_product_add') == 'True'
    #     #
    #     #     if parts_service and not parts_parts:
    #     #         domain.append(('service_type_bool', '=', True))
    #     #     elif parts_parts and not parts_service:
    #     #         domain.append(('service_type_bool', '=', False))
    #     #     elif not parts_service and not parts_parts:
    #     #         # If neither is selected, show no products
    #     #         domain.append(('id', '=', 0))
    #     #
    #     # elif self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #     #     tech_service = self.env['ir.config_parameter'].sudo().get_param('machine_repair_management.technician_service_product_add') == 'True'
    #     #     tech_parts = self.env['ir.config_parameter'].sudo().get_param('machine_repair_management.technician_parts_product_add') == 'True'
    #     #
    #     #     if tech_service and not tech_parts:
    #     #         domain.append(('service_type_bool', '=', True))
    #     #     elif tech_parts and not tech_service:
    #     #         domain.append(('service_type_bool', '=', False))
    #     #     elif not tech_service and not tech_parts:
    #     #         # If neither is selected, show no products
    #     #         domain.append(('id', '=', 0))
    #     #
    #     # if ('service_type_bool', '=', True) in domain:
    #     #     domain.append(('categ_id', '=', self.product_categ_id.id))
    #
    #     # Add location filter if record has location
    #     # print("...........................domain",domain)
    #     # location = False
    #     # print("//////////////////////////////////////location",self.location_id,self.project_task_id)
    #     # if hasattr(self, 'location_id') and self.location_id:
    #     #     location = self.location_id
    #     # elif hasattr(self, 'project_task_id') and self.project_task_id and self.project_task_id.location_id:
    #     #     location = self.project_task_id.location_id
    #     # print("............project",self.project_task_id)
    #     if location:
    #         domain.append(('stock_quant_ids.location_id', '=', location.id))
    #
    #     print("...............after",domain)
    #
    #     return domain

    # @api.onchange('location_id')
    # def _onchange_location_id(self):
    #     domain = []
    #
    #     # 1. User group and config parameter logic
    #     user = self.env.user
    #     config = self.env['ir.config_parameter'].sudo()
    #
    #     # if user.has_group('machine_repair_management.group_technical_allocation_user'):
    #     #     supervisor_service = config.get_param('machine_repair_management.supervisor_service_product_add') == 'True'
    #     #     supervisor_parts = config.get_param('machine_repair_management.supervisor_parts_product_add') == 'True'
    #     #     if supervisor_service and not supervisor_parts:
    #     #         domain.append(('service_type_bool', '=', True))
    #     #     elif supervisor_parts and not supervisor_service:
    #     #         domain.append(('service_type_bool', '=', False))
    #     #     elif not supervisor_service and not supervisor_parts:
    #     #         # Show no products if neither selected
    #     #         domain.append(('id', '=', 0))
    #     #
    #
    #     # Add more user group checks here if relevant (repeat similar for other groups)
    #
    #     # 2. Location filtering logic
    #     print(".............location",self.location_id,self.project_task_id.id,self.location_id.name)
    #     if self.location_id:
    #         quants = self.env['stock.quant'].search([
    #             ('location_id', '=', self.location_id.id),
    #             ('quantity', '>', 0)
    #         ])
    #         product_ids = quants.mapped('product_id').ids
    #         domain.append(('id', 'in', product_ids))
    #
    #     print("............domain",domain)
    #     return {'domain': {'product_id': domain}}

    # @api.model
    # def get_view(self, view_id=None, view_type='form', toolbar=False, submenu=False, **options):
    #     res = super(ProductLine, self).get_view(view_id=view_id, view_type=view_type, toolbar=toolbar,
    #                                          submenu=submenu, **options)
    #
    #     sssssssssssssssssssssssssss
    #
    #     if view_type == ['form']:
    #         domain = []
    #
    #         # 👉 Instead of using a fixed location,
    #         # we reference the field location_id dynamically
    #         domain.append(('stock_quant_ids.location_id', '=', 'parent.location_id'))
    #         print(".......................domain",domain)
    #
    #         # add your existing group + config parameter checks
    #         if self.env.user.has_group('machine_repair_management.group_technical_allocation_user'):
    #             supervisor_service = self.env['ir.config_parameter'].sudo().get_param('machine_repair_management.supervisor_service_product_add') == 'True'
    #             supervisor_parts = self.env['ir.config_parameter'].sudo().get_param('machine_repair_management.supervisor_parts_product_add') == 'True'
    #
    #             if supervisor_service and not supervisor_parts:
    #                 domain.append(('service_type_bool', '=', True))
    #             elif supervisor_parts and not supervisor_service:
    #                 domain.append(('service_type_bool', '=', False))
    #             elif not supervisor_service and not supervisor_parts:
    #                 domain.append(('id', '=', 0))
    #
    #         elif self.env.user.has_group('machine_repair_management.group_parts_user'):
    #             parts_service = self.env['ir.config_parameter'].sudo().get_param('machine_repair_management.parts_service_product_add') == 'True'
    #             parts_parts = self.env['ir.config_parameter'].sudo().get_param('machine_repair_management.parts_user_parts_product_add') == 'True'
    #
    #             if parts_service and not parts_parts:
    #                 domain.append(('service_type_bool', '=', True))
    #             elif parts_parts and not parts_service:
    #                 domain.append(('service_type_bool', '=', False))
    #             elif not parts_service and not parts_parts:
    #                 domain.append(('id', '=', 0))
    #
    #         elif self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #             tech_service = self.env['ir.config_parameter'].sudo().get_param('machine_repair_management.technician_service_product_add') == 'True'
    #             tech_parts = self.env['ir.config_parameter'].sudo().get_param('machine_repair_management.technician_parts_product_add') == 'True'
    #
    #             if tech_service and not tech_parts:
    #                 domain.append(('service_type_bool', '=', True))
    #             elif tech_parts and not tech_service:
    #                 domain.append(('service_type_bool', '=', False))
    #             elif not tech_service and not tech_parts:
    #                 domain.append(('id', '=', 0))
    #
    #         # inject domain into product_id field
    #         if domain:
    #             doc = self.env['ir.ui.view'].browse(view_id).read_combined(['arch'])
    #             arch = doc['arch']
    #             view_arch = etree.XML(arch)
    #
    #             for node in view_arch.xpath("//field[@name='product_id']"):
    #                 node.set("domain", str(domain))
    #
    #             res['arch'] = etree.tostring(view_arch, encoding="unicode")
    #
    #     return res
    #

    ## this is correctly worked but many2mnay is load more time
    # @api.depends('project_task_id', 'location_id')
    # def _compute_product_ids(self):
    #     all_products = self.env['product.product'].with_context(prefetch_fields=False).search([])  # preload all products
    #     for rec in self:
    #         products = all_products
    #
    #         # Filter by user group and service/parts type
    #         if self.env.user.has_group('machine_repair_management.group_technical_allocation_user'):
    #             supervisor_service = self.env['ir.config_parameter'].sudo().get_param('machine_repair_management.supervisor_service_product_add') == 'True'
    #             supervisor_parts = self.env['ir.config_parameter'].sudo().get_param('machine_repair_management.supervisor_parts_product_add') == 'True'
    #
    #             if supervisor_service and not supervisor_parts:
    #                 products = products.filtered(lambda p: p.service_type_bool)
    #             elif supervisor_parts and not supervisor_service:
    #                 products = products.filtered(lambda p: not p.service_type_bool)
    #             # if both True or both False, keep all
    #
    #         elif self.env.user.has_group('machine_repair_management.group_parts_user'):
    #             parts_service = self.env['ir.config_parameter'].sudo().get_param('machine_repair_management.parts_service_product_add') == 'True'
    #             parts_parts = self.env['ir.config_parameter'].sudo().get_param('machine_repair_management.parts_user_parts_product_add') == 'True'
    #
    #             if parts_service and not parts_parts:
    #                 products = products.filtered(lambda p: p.service_type_bool)
    #             elif parts_parts and not parts_service:
    #                 products = products.filtered(lambda p: not p.service_type_bool)
    #
    #         elif self.env.user.has_group('machine_repair_management.group_job_card_mobile_user'):
    #             tech_service = self.env['ir.config_parameter'].sudo().get_param('machine_repair_management.technician_service_product_add') == 'True'
    #             tech_parts = self.env['ir.config_parameter'].sudo().get_param('machine_repair_management.technician_parts_product_add') == 'True'
    #
    #             if tech_service and not tech_parts:
    #                 products = products.filtered(lambda p: p.service_type_bool)
    #             elif tech_parts and not tech_service:
    #                 products = products.filtered(lambda p: not p.service_type_bool)
    #
    #         # Filter by stock location
    #         if rec.location_id:
    #             products = products.filtered(lambda p: any(q.location_id.id == rec.location_id.id for q in p.stock_quant_ids))
    #
    #         rec.product_ids = products

    @api.depends('project_task_id.warehouse_id')
    def _compute_location_id(self):
        for rec in self:
            rec.location_id = rec.project_task_id.warehouse_id.lot_stock_id if rec.project_task_id and rec.project_task_id.warehouse_id else False

    ''' it is commented by Vijaya  bhaskar based on the slowness of the page open on july 10 2025
    def read(self, fields=None, load='_classic_read'):
        res = super(ProductLine, self).read(fields, load)
        for rec in self:
            rec._compute_parts_reserved_qty()
        return res
    '''

    def read(self, fields=None, load='_classic_read'):
        res = super(ProductLine, self).read(fields, load)
        # Only compute if parts_reserved_qty is requested in fields
        if not fields or 'parts_reserved_qty' in fields:
            self._compute_parts_reserved_qty()
        return res

    ''' it is working but warehouse based is not done.so it was commented on Jun 25-2025
    @api.depends('parts_reserved_bool','product_id','project_task_id')
    def _compute_parts_reserved_qty(self):
        for rec in self:
            if rec.parts_reserved_bool and rec.product_id:
                domain = [
                    ('product_id', '=', rec.product_id.id),
                    ('parts_reserved_bool', '=', True),
                    ('project_task_id.job_card_state_code', 'not in', ('126','124'))
                ]

                # Only add id!= condition if this is not a new record
                # if rec.id and isinstance(rec.id, int):
                #     domain.append(('id', '!=', rec.id))
                domain.append(('project_task_id.invoice_no','!=',True))
                reserved_lines = self.env['product.lines'].search(domain)
                rec.parts_reserved_qty = sum(line.qty for line in reserved_lines)
            else:
                rec.parts_reserved_qty = 0.0

    '''

    @api.depends('parts_reserved_bool', 'product_id', 'project_task_id')
    def _compute_parts_reserved_qty(self):
        # Initialize the computed field to 0.0 for all records
        for rec in self:
            rec.parts_reserved_qty = 0.0

        # Filter records that need computation
        valid_records = self.filtered(lambda r: r.parts_reserved_bool and r.product_id)
        if not valid_records:
            return

        # Prepare data for batch query
        product_ids = valid_records.mapped('product_id.id')
        task_ids = valid_records.mapped('project_task_id.id')
        warehouse_ids = valid_records.mapped('project_task_id.warehouse_id.id')
        location_ids = valid_records.mapped('project_task_id.warehouse_id.lot_stock_id.id')

        # Base domain for the query
        domain = [
            ('product_id', 'in', product_ids),
            ('parts_reserved_bool', '=', True),
            ('project_task_id.job_card_state_code', 'not in', ('126', '124')),
            ('project_task_id.invoice_no', '!=', True),
        ]

        # Add location filter if applicable
        if location_ids:
            domain.append(('project_task_id.warehouse_id.lot_stock_id', 'in', location_ids))

        # Use read_group to aggregate quantities by product_id
        grouped_data = self.env['product.lines'].read_group(
            domain,
            ['product_id', 'qty:sum'],
            ['product_id']
        )

        # Map aggregated quantities to records
        qty_by_product = {item['product_id'][0]: item['qty'] for item in grouped_data}

        for rec in valid_records:
            rec.parts_reserved_qty = qty_by_product.get(rec.product_id.id, 0.0)

    ''' it is commented by Vijaya  bhaskar based on the slowness of the page open on july 10 2025
    @api.depends('parts_reserved_bool','product_id','project_task_id')
    def _compute_parts_reserved_qty(self):
        for rec in self:
            if rec.parts_reserved_bool and rec.product_id:
                warehouse = rec.project_task_id.warehouse_id
                location = warehouse.lot_stock_id if warehouse else False

                domain = [
                    ('product_id', '=', rec.product_id.id),
                    ('parts_reserved_bool', '=', True),
                    ('project_task_id.job_card_state_code', 'not in', ('126', '124')),
                    ('project_task_id.invoice_no', '!=', True),
                ]

                if location:
                    domain.append(('project_task_id.warehouse_id.lot_stock_id', '=', location.id))

                reserved_lines = self.env['product.lines'].search(domain)
                rec.parts_reserved_qty = sum(line.qty for line in reserved_lines) 

            else:
                rec.parts_reserved_qty = 0.0

    '''

    # @api.constrains('on_hand_qty')
    # def _valid_check_on_hand_qty(self):
    #     for rec in self:
    #         if rec.on_hand_qty == 0.0:
    #             raise ValidationError("Please Stock is not available.Please Contact Administrator")
    #

    @api.constrains('price_unit')
    def _check_unit_price(self):
        for rec in self:
            if rec.product_id:
                if rec.project_task_id.job_card_state_code != '126':
                    if not rec.under_warranty_bool:
                        if rec.price_unit:
                            if rec.product_id.standard_price > rec.price_unit:
                                raise ValidationError("Unit Price of the product %s is Less than its cost price" % (
                                    rec.product_id.display_name))

    ''' It is working '''

    @api.constrains('parts_reserved_qty', 'on_hand_qty')
    def _valid_check_parts_bool(self):

        if self.env.context.get('from_list_view'):
            return
        for rec in self:
            # if not any(field in rec._get_dirty_fields() for field in ['parts_reserved_qty', 'on_hand_qty']):
            #     continue
            if rec.product_id and rec.parts_reserved_bool and rec.parts_reserved_qty and rec.on_hand_qty:
                if rec.project_task_id.job_card_state_code not in ('126', '124'):
                    if rec.parts_reserved_qty > rec.on_hand_qty:
                        warehouse = rec.project_task_id.warehouse_id
                        location = warehouse.lot_stock_id if warehouse else False
                        # Find all tasks where this product is reserved
                        domain = [
                            ('product_id', '=', rec.product_id.id),
                            ('parts_reserved_bool', '=', True),
                            ('project_task_id.job_card_state_code', 'not in', ('126', '124')),
                            ('project_task_id.invoice_no', '!=', True),
                            ('id', '!=', rec.id)  # Exclude current record
                        ]
                        if location:
                            domain.append(('project_task_id.warehouse_id.lot_stock_id', '=', location.id))

                        reserved_lines = self.env['product.lines'].search(domain)

                        if reserved_lines:
                            task_names = ", ".join(
                                set(line.project_task_id.name for line in reserved_lines if line.project_task_id.name))
                            raise ValidationError(
                                "%s Stock is not available. "
                                "This item is allocated to Job Card No(s): %s"
                                % (rec.product_id.display_name, task_names)
                            )
                        else:
                            raise ValidationError(
                                "Insufficient Stock %s Please Contact Administrator !"
                                % rec.product_id.display_name)
            # if rec.product_id:
            #     if rec.parts_reserved_bool:
            #         if rec.parts_reserved_qty and rec.on_hand_qty:
            #             if rec.parts_reserved_qty > rec.on_hand_qty:
            #                 raise ValidationError("Parts of the product %s is not have valid quantity available " %rec.project_task_id.name)

    @api.model
    def _search_product_for_location(self, location_id):
        quants = self.env['stock.quant'].read_group(
            [('location_id', '=', location_id)],
            ['product_id'],
            ['product_id']
        )
        return [q['product_id'][0] for q in quants if q['product_id']]

    # @api.depends('project_task_id', 'project_task_id.warehouse_id',
    #                 'project_task_id.warehouse_id.lot_stock_id','project_task_id.job_card_state_code')
    # def _compute_product_ids_list(self):
    #     """Compute product_ids by appending product IDs with stock > 0 in warehouse's lot_stock_id or services in same category."""
    #     for rec in self:
    #         # _logger.info("Computing product_ids for record: %s", rec)
    #         rec.product_ids = [(5, 0, 0)]  # Clear existing product_ids
    #
    #         if rec.project_task_id and rec.project_task_id.job_state and rec.project_task_id.job_card_state_code in ('124', '126'):
    #             # _logger.info("Skipping computation as job state is %s", rec.project_task_id.job_card_state_code)
    #             continue
    #
    #         if rec.project_task_id and rec.project_task_id.product_category_id:
    #             categ_id = rec.project_task_id.product_category_id.id
    #             location_id = rec.project_task_id.warehouse_id.lot_stock_id.id if rec.project_task_id.warehouse_id and rec.project_task_id.warehouse_id.lot_stock_id else None
    #
    #             ''' This code is commented on July-07-2025 client asked all the quantity to be shown irrespective of quantity had in the warehouse
    #             query = """
    #                 SELECT DISTINCT p.id
    #                 FROM product_product p
    #                 JOIN product_template pt ON p.product_tmpl_id = pt.id
    #                 WHERE pt.categ_id = %s
    #                 AND p.is_machine = FALSE
    #                 AND (
    #                     (pt.detailed_type = 'service')
    #                     OR
    #                     (%s IS NOT NULL AND p.id IN (
    #                         SELECT sq.product_id
    #                         FROM stock_quant sq
    #                         WHERE sq.location_id = %s AND sq.quantity > 0
    #                     ))
    #                 )
    #             """
    #             params = (categ_id, location_id, location_id) if location_id else (categ_id, None, None)
    #             '''
    #             query = """
    #                 SELECT DISTINCT p.id
    #                 FROM product_product p
    #                 JOIN product_template pt ON p.product_tmpl_id = pt.id
    #                 WHERE pt.categ_id = %s
    #                 AND p.is_machine = FALSE
    #                 AND (
    #                     (pt.detailed_type = 'service')
    #                     OR
    #                     (%s IS NOT NULL AND p.id IN (
    #                         SELECT sq.product_id
    #                         FROM stock_quant sq
    #                     ))
    #                 )
    #             """
    #             params = (categ_id, location_id) if location_id else (categ_id, None, None)
    #
    #             # _logger.debug("Querying products with query: %s and params: %s", query, params)
    #             self.env.cr.execute(query, params)
    #             product_ids = [row['id'] for row in self.env.cr.dictfetchall()]
    #
    #             if product_ids and rec.warehouse_id:
    #                 # Update warehouse_id for products using a single SQL query
    #                 self.env.cr.execute("""
    #                     UPDATE product_product
    #                     SET warehouse_id = %s
    #                     WHERE id IN %s
    #                 """, (rec.warehouse_id.id, tuple(product_ids)))
    #
    #             # _logger.info("Appending product IDs to product_ids: %s", product_ids)
    #             if product_ids:
    #                 rec.product_ids = [(6, 0, product_ids)]  # Append product IDs
    #
    #                 # if rec.warehouse_id:
    #                 #     self.env['product.product'].browse(product_ids).write({'warehouse_id': rec.warehouse_id})
    #             else:
    #                 rec.product_ids = [(5, 0, 0)]  # Ensure empty
    #         else:
    #             _logger.info("No project_task_id or category, setting product_ids to empty")
    #

    ''' Commented By Vijaya bhaskar on June 19- 2025 because they need service also comes under product catgeory.so it is commented 
    @api.depends('project_task_id', 'project_task_id.warehouse_id', 'project_task_id.warehouse_id.lot_stock_id')
    def _compute_product_ids_list(self):
        """Compute product_ids by appending product IDs with stock > 10 in warehouse's lot_stock_id."""
        for rec in self:
            _logger.info("Computing product_ids for record: %s", rec)
            rec.product_ids = [(5, 0, 0)]  # Clear existing product_ids
            if rec.project_task_id and rec.project_task_id.warehouse_id and rec.project_task_id.warehouse_id.lot_stock_id:
                location_id = rec.project_task_id.warehouse_id.lot_stock_id.id
                categ_id = rec.project_task_id.product_category_id.id
                _logger.debug("Querying stock for location_id: %s, category_id: %s", location_id, categ_id)

                # Updated SQL query with OR condition for detailed_type = 'service'
                self.env.cr.execute("""
                    SELECT DISTINCT p.id
                    FROM stock_quant sq
                    JOIN product_product p ON sq.product_id = p.id
                    JOIN product_template pt ON p.product_tmpl_id = pt.id
                    WHERE sq.location_id = %s
                    AND (
                        (sq.quantity > 0 AND p.is_machine = FALSE AND pt.categ_id = %s)

                    )
                """, (location_id, categ_id))
                product_ids = [row['id'] for row in self.env.cr.dictfetchall()]
                _logger.info("Appending product IDs to product_ids: %s", product_ids)
                if product_ids:
                    rec.product_ids = [(6, 0, product_ids)]  # Append product IDs
                else:
                    rec.product_ids = [(5, 0, 0)]  # Ensure empty
            else:
                _logger.info("No project_task_id or warehouse, setting product_ids to empty")
    '''

    ### It will delete the existing record without delete it
    # @api.model
    # def create(self, vals):
    #     # Check for soft-deleted records with the same product_id and uom_id
    #     if 'product_id' in vals and 'uom_id' in vals:
    #         existing = self.with_context(active_test=False).search([
    #             ('product_id', '=', vals.get('product_id')),
    #             ('uom_id', '=', vals.get('uom_id')),
    #
    #         ])
    #         if existing:
    #             # Delete soft-deleted records to avoid constraint violation
    #             existing.unlink()
    #
    #     return super(ProductLine, self).create(vals)

    ''' This code is commented by Vijaya bhaskar on June -12-2025 for asking validation '''

    @api.constrains('product_id', 'uom_id', 'project_task_id')
    def _check_duplicate_service(self):
        for rec in self:
            # Check if this product is already associated with the current job card (defects_id)
            existing_service = self.search([
                ('project_task_id', '=', rec.project_task_id.id),
                ('product_id', '=', rec.product_id.id),
                ('uom_id', '=', rec.uom_id.id),
                ('id', '!=', rec.id),

            ])
            if existing_service:
                raise ValidationError(
                    "This product has already been added to the Product Consume Part/Service for this job card."
                )

    @api.onchange('product_id')
    def _product_line_onchange(self):
        for rec in self:
            quantity = False
            if rec.product_id:
                rec.uom_id = rec.product_id.uom_id
                ''' service product is not go to warranty set up'''
                ''' it is working
                if rec.product_id.detailed_type != 'service':
                    rec.under_warranty_bool = rec.project_task_id.warranty
                else:
                    rec.under_warranty_bool = False
                '''
                '''This is newly added on Jun-19-2025 by VIJAYA BHASKAR'''
                rec.under_warranty_bool = rec.project_task_id.warranty

                '''If Mis use warranty bool then warranty also tick code is added on Oct 17 -2025 '''

                if rec.under_warranty_bool:
                    if rec.project_task_id.service_warranty_id.misuse_warranty_bool:
                        rec.under_warranty_bool = False
                # if rec.under_warranty_bool == True:
                #     rec.total = 0.0
                # else:
                rec.price_unit = rec.product_id.lst_price
                rec.standard_price = rec.product_id.lst_price
                stock_quant_search = self.env['stock.quant'].search([('product_id', '=', rec.product_id.id),
                                                                     ('location_id', '=',
                                                                      rec.project_task_id.warehouse_id.lot_stock_id.id)],
                                                                    limit=1)

                rec.on_hand_qty = stock_quant_search.quantity
                if rec.on_hand_qty == 0.0:
                    raise ValidationError(
                        _("This Product '%s' has no Stock.Please Select another one " % rec.product_id.name))

                '''Overall quantity display added on Aug 20-2025'''
                quanity_search = self.env['stock.quant'].search([('product_id', '=', rec.product_id.id)])
                for quant in quanity_search:
                    quantity += quant.quantity

                rec.overall_qty = quantity

                if rec.product_id.taxes_id:
                    rec.vat = rec.product_id.taxes_id[0].amount
                else:
                    rec.vat = 0.0

                if rec.project_task_id.job_card_state_code == '122':
                    rec.parts_reserved_bool = True

    ''' working code '''

    # @api.onchange('product_id')
    # def _product_line_onchange(self):
    #     for rec in self:
    #         if rec.product_id:
    #             rec.uom_id = rec.product_id.uom_id
    #             ''' service product is not go to warranty set up'''
    #             if rec.product_id.detailed_type != 'service':
    #                 rec.under_warranty_bool = rec.project_task_id.warranty
    #             else:
    #                 rec.under_warranty_bool = False
    #             # if rec.under_warranty_bool == True:
    #             #     rec.total = 0.0
    #             # else:
    #             rec.price_unit = rec.product_id.lst_price
    #             rec.standard_price = rec.product_id.lst_price
    #             if rec.product_id.taxes_id:
    #                 rec.vat = rec.product_id.taxes_id[0].amount
    #             else:
    #                 rec.vat = 0.0

    @api.depends('qty', 'price_unit', 'vat')
    def _compute_total(self):
        for record in self:
            if record.under_warranty_bool == True:
                record.total = 0.0
            else:
                record.total = record.qty * record.price_unit * (1 + (record.vat / 100))
                record.tax_amount = record.qty * record.price_unit * (record.vat / 100)
                # record.tax_amount =  record.tax_amount.quantize(Decimal('0.01'), rounding=ROUND_UP)
                '''service amount is less than 0.01 price so this was added on July 21-2025'''

                # record.tax_amount = Decimal(str(record.tax_amount)).quantize(Decimal('0.01'), rounding=ROUND_UP)
                # record.total = Decimal(str(record.total)).quantize(Decimal('0.01'), rounding=ROUND_UP)

    @api.onchange('under_warranty_bool')
    def _compute_under_warranty_bool(self):
        for rec in self:
            if rec.under_warranty_bool == True:
                rec.total = 0.0
                rec.vat = 0.0
                rec.tax_amount = 0.0
                rec.price_unit = 0.0
            else:
                rec.price_unit = rec.product_id.lst_price
                if rec.product_id.taxes_id:
                    rec.vat = rec.product_id.taxes_id[0].amount
            # if rec.project_task_id.warranty:
            #     rec.under_warranty_bool = rec.project_task_id.warranty
            #     if rec.under_warranty_bool == True:
            #         rec.price_unit = 0.0

    # working code
    # @api.onchange('under_warranty_bool')
    # def _compute_under_warranty_bool(self):
    #     for rec in self:
    #         if rec.under_warranty_bool == True:
    #                 rec.total = 0.0
    #                 # rec.vat = 0.0
    #                 # rec.tax_amount = 0.0
    #         else:
    #             rec.price_unit = rec.product_id.lst_price
    #         # if rec.project_task_id.warranty:
    #         #     rec.under_warranty_bool = rec.project_task_id.warranty
    #         #     if rec.under_warranty_bool == True:
    #         #         rec.price_unit = 0.0
    #

