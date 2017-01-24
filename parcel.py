import base64
import dicttoxml
import subprocess
from datetime import datetime
from flask import jsonify, request, current_app as app

from app.response import Error
from app.models.box import Box
from app.models.parcel import Parcel
from app.models.sender import Sender
from app.models.cellsize import CellSize
from app.controllers.api.parcel import create_one
from app.models.parcel.utils import act_pp
from app.utils.NotifyManager import send_message


class ServiceResponse:
    @classmethod
    def response_json(cls, **kwargs):
        return jsonify(kwargs)

    @classmethod
    def error_response(cls, error):
        return cls.response_json(**dict(result=0, error=error))

    @classmethod
    def success_response(cls, email=0, acts64=None, label64=None,
                         zip64=None, packcodes=None):
        data = dict(
            result=1,
            email=email,
            confirmprintout=[b.decode('ascii') for b in acts64]
                if acts64 else '',
            label=[b.decode('ascii') for b in label64] if label64 else '',
            zip=zip64.decode('ascii') if zip64 else '',
            packcodes=packcodes
        )
        app.logger.debug('Success: {}'.format(data))
        return cls.response_json(**data)


class CreateDeliveryPacks(object):
    sender = None
    files = set()
    labels64 = []
    acts64 = []

    def __init__(self, data):
        self.data = data
        self._authenticate_sender()

    def log(self, msg=None, error=None):
        if msg and app.config.get('DEBUG'):
            app.logger.debug(msg)
        elif error:
            app.logger.error(error)

    def _authenticate_sender(self):
        """
        Sender authentication.
        Spec: FR-1
        :return:
        """
        username = self.data.get('telephonenumber')
        password = self.data.get('password')
        self.sender = Sender.filter_by(
            username=username, password=password).first()
        if self.sender is None:
            raise self.Unauthorized('Authentication failed')

    def create_parcel(self, data):
        self.log(msg=data)
        rec_phone, rec_email, box_, cellsize, amount, barcode, comment = data
        box = Box.filter_by(code=box_).first()
        if box is None:
            raise Error('Почтамат не найден: {}'.format(box_))
        size = CellSize.filter_by(code=cellsize).first()
        if cellsize is None:
            raise Error('Размер посылки не найден: {}'.format(cellsize))

        if isinstance(amount, str):
            if amount.find('.') > 0:
                amount = str(float(amount))
            else:
                amount = ''.join([d for d in amount if d.isdigit()])
        elif amount in (None, ''):
            raise Error('Сумма наложенного платежа не указана')

        parcel, shipment = create_one(dict(
            receiver_phone=rec_phone,
            receiver_email=rec_email,
            box_id=box.id,
            barcode=barcode,
            cellsize=size.code,
            payment_amount=amount,
            sender_id=self.sender.id,
            comment=comment
        ))
        # FNR11-3.6
        parcel.make_barcode(bc=barcode)

        documents = {}
        # FNR11-3.9
        if parcel.shipment_state.code == 'Created':
            parcel.set_shipment_state('Prepared', save=True)

        return parcel, shipment

    def get_label(self, parcel, return_path=True, b64=False):
        try:
            result = parcel.make_sticker(
                pdf=True, b64=b64, return_path=return_path)
            path = None
            data64 = None
            if return_path and b64:
                path, data64 = result
                self.files.add(path)
                self.labels64.append(data64)
            elif return_path:
                path = result
                self.files.add(path)
            elif b64:
                data64 = result
                self.labels64.append(data64)
            return path, data64
        except Exception as e:
            app.logger.warning('Sticker generation error: {}'.format(e))
            raise Error('Ошибка печати этикетки для посылки: {}'.format(
                parcel.barcode))

    def get_act_pp(self, parcel, shipment, b64=False):
        try:
            result = act_pp(parcel, shipment, b64=b64)
            data64 = None
            if b64:
                path, data64 = result
                self.files.add(path)
                self.acts64.append(data64)
            else:
                path = result
                self.files.add(path)
            return path, data64
        except Exception as e:
            self.log(error='Act PP generation error: {}'.format(e))
            raise Error('Ошибка формирования Акта-ПП для посылки: {}'
                        ''.format(parcel.barcode))

    def get_zip(self, parcel, shipment, return_path=False):
        label_path, label_data64 = self.get_label(
            parcel, return_path=True, b64=True)
        act_pp_path, act_pp_data64 = self.get_act_pp(parcel, shipment, b64=True)
        zip_path = '/tmp/{}.zip'.format(parcel.barcode)
        subprocess.Popen([
            'zip', zip_path, label_path, act_pp_path, '-j'
        ]).wait()
        if return_path:
            return zip_path
        with open(zip_path, 'rb') as zip_file:
            return base64.b64encode(zip_file.read())

    def main(self):
        data = self.data
        self.log(msg='parcel.createdeliverypacks({})'.format(data))

        parcels = data['parcels']
        email = data.get('email')
        label = str(data.get('label')) == '1'
        confirmprintout = str(data.get('confirmprintout')) == '1'
        gz = str(data.get('zip')) == '1'
        packcodes = str(data.get('packcodes')) == '1'
        response_format = data.get('type')
        test = data.get('test')

        created_parcels = []
        to_zip = []
        packcodes_list = []

        parcels = parcels.replace('\\r', '')
        parcels = parcels.split('\\n')
        if not parcels:
            app.logger.error('Empty parcels list: {}'.format(data['parcels']))
            return ServiceResponse.error_response('Не передан список посылок!')

        app.logger.debug('Parcels: {}'.format(parcels))

        for parcel_data in parcels:
            if not parcel_data:
                continue
            try:
                parcel, shipment = self.create_parcel(parcel_data.split(';'))
                if packcodes:
                    packcodes_list.append(parcel.barcode)
                if gz or email:
                    to_zip.append(self.get_zip(parcel, shipment,
                                               return_path=True))
                else:
                    if label:
                        self.get_label(parcel, b64=True)
                    if confirmprintout:
                        self.get_act_pp(parcel, shipment, b64=True)
                created_parcels.append([parcel.barcode, parcel.str_id])
            except Error as e:
                app.logger.error('Failed while parse: {}'.format(parcel_data))
                return ServiceResponse.error_response(e.message)

        kwargs = dict(
            zip64=b''
        )
        if packcodes:
            kwargs['packcodes'] = packcodes_list
        if label:
            kwargs['label64'] = self.labels64
        if confirmprintout:
            kwargs['acts64'] = self.acts64
        if self.files and email:
            fn = datetime.now().strftime('%Y%m%d%H%M%S')
            zip_path = '/tmp/{}.zip'.format(fn)
            args = ['zip', zip_path, '-j']
            args.extend(self.files)
            self.log(msg=' '.join(args))
            subprocess.Popen(args).wait()
            with open(zip_path, 'rb') as zip_file:
                kwargs['zip64'] = base64.b64encode(zip_file.read())
            if email:
                send_message(
                    'email',
                    subject='createdeliverypacks',
                    recipient=email.split(','),
                    message='See attachment',
                    html_message='See attachment',
                    files=[zip_path],
                    directly=True
                )
        kwargs['email'] = 1 if email else 0

        return ServiceResponse.success_response(**kwargs)

    class Unauthorized(Error):
        pass


def createdeliverypacks():
    create_delivery_packs = CreateDeliveryPacks(request.values.to_dict())
    try:
        return create_delivery_packs.main()
    except CreateDeliveryPacks.Unauthorized:
        app.logger.error('Unauthorized access: {}={}'.format(
                request.values.to_dict()))
        ServiceResponse.error_response('Неверный логин и/или пароль')


def change_packsize():
    username = request.values.get('telephonenumber')
    password = request.values.get('password')
    sender = Sender.filter_by(username=username, password=password).first()
    if sender is None:
        return '-401', 401

    packcode = request.values.get('packcode')
    packsize = request.values.get('packsize')

    parcel = Parcel.get_by_id(packcode)
    if parcel.shipment_state.code != 'Created':
        return 0, 400
    if parcel is None:
        return '-1', 400
    cellsize = CellSize.query.get(packsize)
    if cellsize is None:
        return '-2', 400
    cell = parcel.cell
    cell.size_id = cellsize.id
    cell.save()
    return 1, 200


def getpackstatus():
    username = request.values.get('telephonenumber')
    password = request.values.get('password')
    sender = Sender.filter_by(username=username, password=password).first()
    if sender is None:
        return '-401', 401

    packcode = request.values.get('packcode')
    parcel = Parcel.filter_by(barcode=packcode).first()
    if parcel is None:
        return -1, 404
    return jsonify({'status': parcel.shipment_state.code})


def simpletrack():
    username = request.values.get('telephonenumber')
    password = request.values.get('password')
    sender = Sender.filter_by(username=username, password=password).first()
    if sender is None:
        return '-401', 401

    packcode = request.values.get('packCode')
    parcels_ids = packcode.split(',')
    parcels = Parcel.filter(Parcel.barcode.in_(parcels_ids)).first()
    csv = []
    for parcel in parcels:
        csv.append('{};{};{}'.format(parcel.str_id,
                                     parcel.shipment_state.code,
                                     parcel.shipment_state.name))
    return '\n'.join(csv), 200