from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

NAVY   = colors.HexColor('#1E4D8C')
LBLUE  = colors.HexColor('#EBF3FF')
BORDER = colors.HexColor('#CCCCCC')
WHITE  = colors.white
BLACK  = colors.black
GREY   = colors.HexColor('#555555')

def _style(name, **kw):
    return ParagraphStyle(name, **kw)

def generate_invoice_pdf(inv, path):
    """Generate a professional GST invoice PDF."""
    doc = SimpleDocTemplate(path, pagesize=A4,
                            leftMargin=14*mm, rightMargin=14*mm,
                            topMargin=10*mm, bottomMargin=14*mm)
    W = A4[0] - 28*mm
    co = inv.get('company', {})
    items = inv.get('items', [])
    story = []

    # ── Header ─────────────────────────────────────────────────────────────
    h_left = _style('HL', fontName='Helvetica-Bold', fontSize=13, textColor=WHITE, leading=17)
    h_info = _style('HI', fontName='Helvetica', fontSize=8, textColor=WHITE, leading=11)
    h_right = _style('HR', fontName='Helvetica', fontSize=9, textColor=WHITE,
                     alignment=TA_RIGHT, leading=13)

    def hp(s, sty): return Paragraph(s.replace('\n','<br/>'), sty)

    hdr = Table([[
        [Paragraph(f'<b>{co.get("name","")}</b>', h_left),
         Paragraph(co.get('address',''), h_info),
         Paragraph(f'GSTIN: {co.get("gstin","")}  |  Ph: {co.get("phone","")}', h_info)],
        [Paragraph('<b>TAX INVOICE</b>', _style('T', fontName='Helvetica-Bold', fontSize=14,
                                                textColor=WHITE, alignment=TA_RIGHT)),
         Paragraph(f'<b>Invoice No:</b> {inv.get("invoice_no","")}', h_right),
         Paragraph(f'<b>Date:</b> {inv.get("date","")}', h_right),
         Paragraph(f'<b>Type:</b> {inv.get("invoice_type","TAX INVOICE")}', h_right),
         Paragraph('<b>Original Copy</b>', h_right)]
    ]], colWidths=[W*0.58, W*0.42])
    hdr.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), NAVY),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0,0), (0,0), 10),
        ('RIGHTPADDING', (-1,-1), (-1,-1), 10),
        ('TOPPADDING', (0,0), (-1,-1), 10),
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
    ]))
    story.append(hdr)
    story.append(Spacer(1, 3*mm))

    # ── Bill To ─────────────────────────────────────────────────────────────
    bt_sty = _style('BT', fontName='Helvetica', fontSize=8.5, leading=13)
    ps_sty = _style('PS', fontName='Helvetica', fontSize=8.5, leading=13, alignment=TA_RIGHT)

    phone_html = f'<br/><b>Phone:</b> {inv.get("customer_phone")}' if inv.get("customer_phone") else ''
    
    bt = Table([[
        Paragraph(f'<b>Bill To:</b><br/><b>{inv.get("customer_name","")}</b><br/>'
                  f'{inv.get("customer_address","")}<br/>'
                  f'<b>GSTIN:</b> {inv.get("customer_gstin","")}{phone_html}', bt_sty),
        Paragraph(f'<b>Place of Supply:</b> {inv.get("place_of_supply","24-Gujarat")}',
                  ps_sty)
    ]], colWidths=[W*0.65, W*0.35])
    bt.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), LBLUE),
        ('BOX', (0,0), (-1,-1), 0.5, BORDER),
        ('TOPPADDING', (0,0), (-1,-1), 8), ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('LEFTPADDING', (0,0), (-1,-1), 8), ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]))
    story.append(bt)
    story.append(Spacer(1, 3*mm))

    # ── Items Table ─────────────────────────────────────────────────────────
    th_sty = _style('TH', fontName='Helvetica-Bold', fontSize=8, textColor=WHITE,
                    alignment=TA_CENTER)
    td_sty = _style('TD', fontName='Helvetica', fontSize=8.5, leading=11)
    td_r  = _style('TDR', fontName='Helvetica', fontSize=8.5, alignment=TA_RIGHT)
    td_c  = _style('TDC', fontName='Helvetica', fontSize=8.5, alignment=TA_CENTER)

    def th(t): return Paragraph(t, th_sty)
    def td(t): return Paragraph(str(t), td_sty)
    def tdr(t): return Paragraph(str(t), td_r)
    def tdc(t): return Paragraph(str(t), td_c)

    show_customer = inv.get('show_customer', False)
    show_date = inv.get('show_date', False)

    has_gst = False
    for item in items:
        if float(item.get('gst_percent', 0)) > 0 or float(item.get('cgst', 0)) > 0 or float(item.get('sgst', 0)) > 0 or float(item.get('igst', 0)) > 0:
            has_gst = True
            break

    if has_gst:
        if show_customer and show_date:
            cws = [W*.04, W*.14, W*.10, W*.16, W*.08, W*.06, W*.08, W*.06, W*.14, W*.14]
            headers = [th('Sr.'), th('Product Name'), th('Date'), th('Customer'), th('HSN/SAC'), th('Qty'), th('Rate (Rs.)'), th('GST %'), th('Taxable (Rs.)'), th('Amount (Rs.)')]
        elif show_customer:
            cws = [W*.05, W*.16, W*.20, W*.08, W*.07, W*.08, W*.07, W*.14, W*.15]
            headers = [th('Sr.'), th('Product Name'), th('Customer'), th('HSN/SAC'), th('Qty'), th('Rate (Rs.)'), th('GST %'), th('Taxable (Rs.)'), th('Amount (Rs.)')]
        elif show_date:
            cws = [W*.05, W*.20, W*.12, W*.08, W*.07, W*.09, W*.07, W*.16, W*.16]
            headers = [th('Sr.'), th('Product Name'), th('Date'), th('HSN/SAC'), th('Qty'), th('Rate (Rs.)'), th('GST %'), th('Taxable (Rs.)'), th('Amount (Rs.)')]
        else:
            cws = [W*.05, W*.28, W*.10, W*.08, W*.10, W*.07, W*.16, W*.16]
            headers = [th('Sr.'), th('Product Name'), th('HSN/SAC'), th('Qty'), th('Rate (Rs.)'), th('GST %'), th('Taxable (Rs.)'), th('Amount (Rs.)')]
    else:
        if show_customer and show_date:
            cws = [W*.04, W*.23, W*.10, W*.16, W*.08, W*.06, W*.15, W*.18]
            headers = [th('Sr.'), th('Product Name'), th('Date'), th('Customer'), th('HSN/SAC'), th('Qty'), th('Rate (Rs.)'), th('Amount (Rs.)')]
        elif show_customer:
            cws = [W*.05, W*.25, W*.20, W*.08, W*.07, W*.14, W*.21]
            headers = [th('Sr.'), th('Product Name'), th('Customer'), th('HSN/SAC'), th('Qty'), th('Rate (Rs.)'), th('Amount (Rs.)')]
        elif show_date:
            cws = [W*.05, W*.28, W*.12, W*.08, W*.07, W*.14, W*.26]
            headers = [th('Sr.'), th('Product Name'), th('Date'), th('HSN/SAC'), th('Qty'), th('Rate (Rs.)'), th('Amount (Rs.)')]
        else:
            cws = [W*.05, W*.38, W*.10, W*.08, W*.15, W*.24]
            headers = [th('Sr.'), th('Product Name'), th('HSN/SAC'), th('Qty'), th('Rate (Rs.)'), th('Amount (Rs.)')]

    rows_data = [headers]

    total_taxable = total_cgst = total_sgst = total_igst = 0.0

    for i, item in enumerate(items, 1):
        qty   = float(item.get('qty', 0))
        rate  = float(item.get('rate', 0))
        taxbl = float(item.get('taxable_amount', 0))
        cgst  = float(item.get('cgst', 0))
        sgst  = float(item.get('sgst', 0))
        igst  = float(item.get('igst', 0))
        gst_p = float(item.get('gst_percent', 18))
        amt   = taxbl + cgst + sgst + igst
        total_taxable += taxbl
        total_cgst += cgst
        total_sgst += sgst
        total_igst += igst

        row = [
            tdc(str(i)),
            td(item.get('product_name',''))
        ]
        
        date_str = item.get('date','')
        if date_str and len(date_str) >= 10:
            parts = date_str.split('-')
            if len(parts) == 3: date_str = f"{parts[2]}/{parts[1]}/{parts[0]}"
                
        if show_customer and show_date:
            row.append(tdc(date_str))
            row.append(td(item.get('customer_name','')))
        elif show_customer:
            row.append(td(item.get('customer_name','')))
        elif show_date:
            row.append(tdc(date_str))
            
        row.extend([
            tdc(item.get('hsn_code','')),
            tdr(f'{qty:.0f}'),
            tdr(f'{rate:.2f}')
        ])
        
        if has_gst:
            row.extend([
                tdc(f'{gst_p:.0f}%'),
                tdr(f'{taxbl:,.2f}')
            ])
            
        row.append(tdr(f'{amt:,.2f}'))
        
        rows_data.append(row)

    total_qty = sum(float(item.get('qty', 0)) for item in items)
    tdr_b = _style('TDRB', fontName='Helvetica-Bold', fontSize=8.5, alignment=TA_RIGHT)
    
    if show_customer and show_date:
        total_row = ['', '', '', '', Paragraph('Total:', tdr_b), Paragraph(f'{total_qty:.0f}', tdr_b)]
    elif show_customer or show_date:
        total_row = ['', '', '', Paragraph('Total:', tdr_b), Paragraph(f'{total_qty:.0f}', tdr_b)]
    else:
        total_row = ['', '', Paragraph('Total:', tdr_b), Paragraph(f'{total_qty:.0f}', tdr_b)]
        
    total_row.extend([''] * (len(headers) - len(total_row)))
    rows_data.append(total_row)

    it = Table(rows_data, colWidths=cws, repeatRows=1)
    row_count = len(rows_data)
    it.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), NAVY),
        ('TEXTCOLOR', (0,0), (-1,0), WHITE),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [WHITE, LBLUE]),
        ('GRID', (0,0), (-1,-1), 0.5, BORDER),
        ('TOPPADDING', (0,0), (-1,-1), 5), ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('LEFTPADDING', (0,0), (-1,-1), 4), ('RIGHTPADDING', (0,0), (-1,-1), 4),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(it)
    story.append(Spacer(1, 3*mm))

    # ── Tax Summary ─────────────────────────────────────────────────────────
    grand = float(inv.get('grand_total', 0))
    lbl = _style('L', fontName='Helvetica', fontSize=8.5)
    lbl_b = _style('LB', fontName='Helvetica-Bold', fontSize=9)
    val = _style('V', fontName='Helvetica', fontSize=8.5, alignment=TA_RIGHT)
    val_b = _style('VB', fontName='Helvetica-Bold', fontSize=9, alignment=TA_RIGHT)

    tax_rows = [
        ['', Paragraph('Sub Total (Taxable Amount)', lbl),
             Paragraph(f'Rs. {total_taxable:,.2f}', val)],
    ]
    if total_cgst > 0:
        tax_rows.append(['', Paragraph('Central Tax (CGST @ 9%)', lbl),
                              Paragraph(f'Rs. {total_cgst:,.2f}', val)])
        tax_rows.append(['', Paragraph('State/UT Tax (SGST @ 9%)', lbl),
                              Paragraph(f'Rs. {total_sgst:,.2f}', val)])
    if total_igst > 0:
        tax_rows.append(['', Paragraph('Integrated Tax (IGST @ 18%)', lbl),
                              Paragraph(f'Rs. {total_igst:,.2f}', val)])
    tax_rows.append(['', Paragraph('GRAND TOTAL', lbl_b),
                         Paragraph(f'Rs. {grand:,.2f}', val_b)])

    tax_t = Table(tax_rows, colWidths=[W*0.47, W*0.35, W*0.18])
    last = len(tax_rows) - 1
    tax_t.setStyle(TableStyle([
        ('BACKGROUND', (0, last), (-1, last), NAVY),
        ('TEXTCOLOR', (0, last), (-1, last), WHITE),
        ('ROWBACKGROUNDS', (0,0), (-1, last-1), [WHITE, LBLUE]),
        ('BOX', (1, 0), (-1, -1), 0.5, BORDER),
        ('LINEABOVE', (0, last), (-1, last), 1, NAVY),
        ('TOPPADDING', (0,0), (-1,-1), 4), ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LEFTPADDING', (0,0), (-1,-1), 5), ('RIGHTPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(tax_t)
    story.append(Spacer(1, 3*mm))

    # ── Amount in Words ─────────────────────────────────────────────────────
    total_gst = total_cgst + total_sgst + total_igst
    w_sty = _style('W', fontName='Helvetica', fontSize=8.5, leading=13)
    words_t = Table([[
        Paragraph(f'<b>Bill Amount:</b> {inv.get("amount_words","")}', w_sty),
        Paragraph(f'<b>Total GST:</b> {inv.get("gst_words","")}', w_sty),
    ]], colWidths=[W*0.6, W*0.4])
    words_t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), LBLUE),
        ('BOX', (0,0), (-1,-1), 0.5, BORDER),
        ('TOPPADDING', (0,0), (-1,-1), 6), ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 8), ('RIGHTPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(words_t)
    story.append(Spacer(1, 3*mm))

    # ── Bank Details ─────────────────────────────────────────────────────────
    bank_name = co.get('bank_name', '')
    if bank_name:
        bk = Table([[Paragraph(
            f'<b>Bank:</b> {bank_name} &nbsp;|&nbsp; '
            f'<b>A/C:</b> {co.get("bank_account","")} &nbsp;|&nbsp; '
            f'<b>IFSC:</b> {co.get("bank_ifsc","")} &nbsp;|&nbsp; '
            f'<b>Branch:</b> {co.get("bank_branch","")}',
            _style('BK', fontName='Helvetica', fontSize=7.5, textColor=GREY)
        )]], colWidths=[W])
        bk.setStyle(TableStyle([
            ('BOX', (0,0), (-1,-1), 0.5, BORDER),
            ('TOPPADDING', (0,0), (-1,-1), 5), ('BOTTOMPADDING', (0,0), (-1,-1), 5),
            ('LEFTPADDING', (0,0), (-1,-1), 8),
        ]))
        story.append(bk)
        story.append(Spacer(1, 3*mm))

    # ── Terms & Signatory ─────────────────────────────────────────────────
    terms = co.get('terms', '')
    terms_html = '<br/>'.join(
        f'<font size="7.5">{ln}</font>'
        for ln in terms.split('\n') if ln.strip()
    )
    sign_sty = _style('S', fontName='Helvetica', fontSize=8.5,
                      alignment=TA_CENTER, leading=13)

    bottom = Table([[
        Paragraph(f'<b>Terms &amp; Conditions:</b><br/>{terms_html}',
                  _style('TC', fontName='Helvetica', fontSize=7.5, leading=11)),
        Paragraph(
            f'<b>For, {co.get("name","")}</b>'
            '<br/><br/><br/><br/>_________________________<br/><b>Authorised Signatory</b>',
            sign_sty)
    ]], colWidths=[W*0.6, W*0.4])
    bottom.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 0.5, BORDER),
        ('LINEAFTER', (0,0), (0,-1), 0.5, BORDER),
        ('TOPPADDING', (0,0), (-1,-1), 8), ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('LEFTPADDING', (0,0), (-1,-1), 8), ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]))
    story.append(bottom)

    # Footer note
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(
        f'This is a computer generated invoice. | {co.get("name","")} | GSTIN: {co.get("gstin","")}',
        _style('FT', fontName='Helvetica', fontSize=7, textColor=GREY, alignment=TA_CENTER)
    ))

    doc.build(story)
