from flask import current_app, render_template, abort, Blueprint, make_response, url_for
import os
import codecs
from .pki.models import Certificate
from .profiles.cert import PEMCertificatePayload, SCEPPayload
from .profiles.mdm import MDMPayload
from .profiles import Profile
from .models import db, Organization, SCEPConfig
from sqlalchemy.orm.exc import NoResultFound, MultipleResultsFound

PROFILE_CONTENT_TYPE = 'application/x-apple-aspen-config'

enroll_app = Blueprint('enroll_app', __name__)


@enroll_app.route('/')
def index():
    """Show the enrollment page"""
    return render_template('enroll.html')


def base64_to_pem(crypto_type, b64_text, width=76):
    lines = ''
    for pos in range(0, len(b64_text), width):
        lines += b64_text[pos:pos + width] + '\n'

    return '-----BEGIN %s-----\n%s-----END %s-----' % (crypto_type, lines, crypto_type)


@enroll_app.route('/profile', methods=['GET', 'POST'])
def enroll():
    """Generate an enrollment profile."""
    try:
        org = db.session.query(Organization).one()
    except NoResultFound:
        abort(500, 'No organization is configured, cannot generate enrollment profile.')
    except MultipleResultsFound:
        abort(500, 'Multiple organizations, backup your database and start again')

    push_path = os.path.join(os.path.dirname(current_app.root_path), current_app.config['PUSH_CERTIFICATE'])

    try:
        scep_config = db.session.query(SCEPConfig).one()
    except NoResultFound:
        abort(500, 'No SCEP Configuration found, cannot generate enrollment profile.')

    if os.path.exists(push_path):
        with open(push_path, 'rb') as fd:
            push_cert = Certificate('mdm.pushcert')
            push_cert.pem_data = fd.read()
    else:
        abort(500, 'No push certificate available at: {}'.format(push_path))

    if not org:
        abort(500, 'No MDM configuration present; cannot generate enrollment profile')

    if not org.payload_prefix:
        abort(500, 'MDM configuration has no profile prefix')

    profile = Profile(org.payload_prefix + '.enroll', PayloadDisplayName=org.name)

    # ca_cert_payload = PEMCertificatePayload(org.payload_prefix + '.mdm-ca', mdm_ca.certificate.pem_data,
    #                                         PayloadDisplayName='MDM CA Certificate')
    #
    # profile.append_payload(ca_cert_payload)


    # Include Self Signed Certificate if necessary
    # TODO: Check that cert is self signed.
    if 'SSL_CERTIFICATE' in current_app.config:
        basepath = os.path.dirname(__file__)
        certpath = os.path.join(basepath, current_app.config['SSL_CERTIFICATE'])
        with open(certpath, 'rb') as fd:
            pem_data = fd.read()
            pem_payload = PEMCertificatePayload(org.payload_prefix + '.ssl', pem_data, PayloadDisplayName='Web Server Certificate')
            profile.append_payload(pem_payload)

    scep_payload = SCEPPayload(
        org.payload_prefix + '.mdm-scep',
        scep_config.url,
        PayloadContent=dict(
            Keysize=2048,
            # Challenge=scep_config.challenge,
            Subject=[
                [['CN', '%HardwareUUID%']]
            ]
        ),
        PayloadDisplayName='MDM SCEP')
    profile.append_payload(scep_payload)
    cert_uuid = scep_payload.get_uuid()
    # else:
    #     abort(500, 'Invalid device identity method')

    from .mdm import AccessRights

    new_mdm_payload = MDMPayload(
        org.payload_prefix + '.mdm',
        cert_uuid,
        push_cert.topic,  # APNs push topic
        url_for('mdm_app.mdm', _external=True, _scheme='https'),
        AccessRights.All,
        CheckInURL='https://localhost:5443/checkin',
        # CheckInURL=url_for('mdm_app.checkin', _external=True, _scheme='https'),
        # we can validate MDM device client certs provided via SSL/TLS.
        # however this requires an SSL framework that is able to do that.
        # alternatively we may optionally have the client digitally sign the
        # MDM messages in an HTTP header. this method is most portable across
        # web servers so we'll default to using that method. note it comes
        # with the disadvantage of adding something like 2KB to every MDM
        # request
        SignMessage=True,
        CheckOutWhenRemoved=True,
        ServerCapabilities=['com.apple.mdm.per-user-connections'],
        # per-network user & mobile account authentication (OS X extensions)
        PayloadDisplayName='Device Configuration and Management')

    profile.append_payload(new_mdm_payload)

    resp = make_response(profile.generate_plist())
    resp.headers['Content-Type'] = PROFILE_CONTENT_TYPE
    return resp


# def device_first_post_enroll(device, awaiting=False):
#     print('enroll:', 'UpdateInventoryDevInfoCommand')
#     db.session.add(UpdateInventoryDevInfoCommand.new_queued_command(device))
#
#     # install all group profiles
#     for group in device.mdm_groups:
#         for profile in group.profiles:
#             db.session.add(InstallProfile.new_queued_command(device, {'id': profile.id}))
#
#     if awaiting:
#         # in DEP Await state, send DeviceConfigured to proceed with setup
#         db.session.add(DeviceConfigured.new_queued_command(device))
#
#     db.session.commit()
#
#     push_to_device(device)