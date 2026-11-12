"""Справочник контрагентов из главной базы «1С:Предприятие»."""
from flask import Blueprint, render_template
from flask_login import login_required

from engine.models import Contractor

bp = Blueprint('contractors', __name__)


@bp.route('/contractors')
@login_required
def list_contractors():
    """Список контрагентов из Catalog_Контрагенты."""
    contractors = Contractor.query.order_by(Contractor.full_name).all()
    return render_template('contractors/list.html',
                           contractors=contractors,
                           contractors_total=len(contractors))
