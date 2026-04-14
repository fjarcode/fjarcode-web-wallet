import secrets
import base64
import io
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from cashaddress.convert import InvalidAddress
from django.contrib.auth.hashers import check_password, make_password
from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.core.paginator import Paginator
from django.core.signing import BadSignature
from django.http import JsonResponse
from django.middleware.csrf import rotate_token
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from mnemonic import Mnemonic
from webauthn import (
	generate_authentication_options,
	generate_registration_options,
	options_to_json,
	verify_authentication_response,
	verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
	AuthenticatorAttachment,
	AuthenticatorSelectionCriteria,
	PublicKeyCredentialDescriptor,
	ResidentKeyRequirement,
	UserVerificationRequirement,
)

from wallet.services.addresses import derive_fjar_addresses, to_fjarcode_cashaddr
from wallet.services.electrum import ElectrumClient, ElectrumConnectionError
from wallet.services.sender import WalletSendError, prepare_send_preview, sign_and_broadcast


DEFAULT_FEE_LEVEL = '2'
ESTIMATED_TX_VBYTES = 220
FEE_RATE_SAT_VB = {
	'1': 1,
	'2': 2,
	'3': 4,
}
FEE_LEVEL_NAME = {
	'1': 'Low',
	'2': 'Medium',
	'3': 'High',
}
COINBASE_MATURITY_CONFIRMATIONS = 100
TX_PAGE_SIZE = 10
PASSKEY_REGISTER_CHALLENGE_SESSION_KEY = 'passkey_register_challenge'
PASSKEY_AUTH_CHALLENGE_SESSION_KEY = 'passkey_auth_challenge'
WALLET_REF_COOKIE_NAME = 'wallet_ref'
logger = logging.getLogger(__name__)


def _build_receive_qr_data_uri(payload):
	if not payload:
		return None

	try:
		import qrcode
		from PIL import Image, ImageDraw
		from qrcode.constants import ERROR_CORRECT_H
	except Exception:  # noqa: BLE001
		return None

	try:
		qr = qrcode.QRCode(
			version=None,
			error_correction=ERROR_CORRECT_H,
			box_size=12,
			border=2,
		)
		qr.add_data(payload)
		qr.make(fit=True)
		img = qr.make_image(fill_color='#ff4545', back_color='#05080b').convert('RGBA')

		logo_path = settings.BASE_DIR / 'static' / 'img' / 'fjarcode.png'
		if logo_path.exists():
			logo = Image.open(logo_path).convert('RGBA')
			logo_max = max(int(img.size[0] * 0.2), 42)
			logo.thumbnail((logo_max, logo_max), Image.LANCZOS)

			pad = max(6, logo_max // 8)
			badge_w = logo.width + (pad * 2)
			badge_h = logo.height + (pad * 2)
			badge = Image.new('RGBA', (badge_w, badge_h), (5, 8, 11, 235))
			draw = ImageDraw.Draw(badge)
			draw.rectangle((0, 0, badge_w - 1, badge_h - 1), outline=(255, 69, 69, 220), width=2)
			badge.paste(logo, (pad, pad), logo)

			pos = ((img.width - badge.width) // 2, (img.height - badge.height) // 2)
			img.paste(badge, pos, badge)

		buf = io.BytesIO()
		img.save(buf, format='PNG', optimize=True)
		encoded = base64.b64encode(buf.getvalue()).decode('ascii')
		return f'data:image/png;base64,{encoded}'
	except Exception:  # noqa: BLE001
		return None


def _lang(request):
	return 'is' if request.GET.get('lang') == 'is' else 'en'


def _t(request, en_text, is_text):
	return is_text if _lang(request) == 'is' else en_text


def _lang_context(request):
	return {
		'lang': _lang(request),
		'active_nav': 'home',
		'labels': {
			'home_title': _t(request, 'Web Wallet', 'Vefveski'),
			'home_sub': _t(request, 'Non-custodial wallet setup', 'Uppsetning á non-custodial veski'),
			'home_info_button': _t(request, 'Info', 'Upplýsingar'),
			'home_info_title': _t(request, 'Before You Create a Wallet', 'Áður en þú býrð til veski'),
			'home_info_intro': _t(
				request,
				'This is a non-custodial wallet. You are fully responsible for your recovery data.',
				'Þetta er non-custodial veski. Þú berð fulla ábyrgð á endurheimtargögnunum þínum.',
			),
			'home_info_seed': _t(
				request,
				'Write down your seed phrase offline and keep it private.',
				'Skrifaðu seed frasann niður offline og geymdu hann leynilegan.',
			),
			'home_info_loss': _t(
				request,
				'If you lose the seed phrase, your wallet cannot be recovered.',
				'Ef þú tapar seed frasanum er ekki hægt að endurheimta veskið.',
			),
			'home_info_device': _t(
				request,
				'Keep your device secure and use a strong wallet password.',
				'Haltu tækinu þínu öruggu og notaðu sterkt lykilorð fyrir veskið.',
			),
			'home_info_open_source': _t(request, 'This wallet is open source:', 'Þetta veski er open source:'),
			'home_info_repo_label': _t(request, 'GitHub Repository', 'GitHub gagnasafn'),
			'home_info_continue': _t(request, 'I Understand', 'Ég skil'),
			'create': _t(request, 'Create New Wallet', 'Búa til nýtt veski'),
			'recover': _t(request, 'Recover From Seed Phrase', 'Endurheimta úr seed frasa'),
			'back': _t(request, 'Back', 'Til baka'),
			'seed_phrase': _t(request, 'Seed Phrase', 'Seed frasi'),
			'seed_words': _t(request, 'Seed Length', 'Lengd seed frasa'),
			'seed_words_12': _t(request, '12 words', '12 orð'),
			'seed_words_24': _t(request, '24 words', '24 orð'),
			'seed_regenerate': _t(request, 'Update Seed', 'Uppfæra seed'),
			'created': _t(request, 'Wallet created.', 'Veski búið til.'),
			'seed_confirm_prompt': _t(request, 'Confirm before continuing', 'Staðfesta áður en haldið er áfram'),
			'seed_confirm_button': _t(request, 'I have written down my seed phrase.', 'Ég hef skrifað seed frasann niður.'),
			'seed_confirm_required': _t(request, 'Please confirm that you wrote down the seed phrase.', 'Staðfestu að þú hafir skrifað seed frasann niður.'),
			'wallet_password': _t(request, 'Create wallet password', 'Lykilorð fyrir veski'),
			'unlock_password': _t(request, 'Wallet Password', 'Lykilorð fyrir veski'),
			'wallet_password_hint': _t(request, 'Minimum 6 characters.', 'Lágmark 6 stafir.'),
			'wallet_password_required': _t(request, 'Password is required (min 6 characters).', 'Lykilorð er nauðsynlegt (lágmark 6 stafir).'),
			'unlock_title': _t(request, 'Unlock Wallet', 'Opna veski'),
			'unlock_button': _t(request, 'Unlock', 'Opna'),
			'unlock_invalid': _t(request, 'Invalid wallet password.', 'Ógilt lykilorð fyrir veski.'),
			'unlock_forgot_hint': _t(
				request,
				'Forgot password? You can disconnect this wallet session and recover again from your seed phrase.',
				'Gleymdir þú lykilorði? Þú getur aftengt þessa veskislotu og endurheimt aftur með seed frasanum.',
			),
			'unlock_reset_cta': _t(request, 'Disconnect Wallet', 'Aftengja veski'),
			'unlock_reset_title': _t(request, 'Disconnect wallet?', 'Aftengja veski?'),
			'unlock_reset_confirm': _t(
				request,
				'Disconnect wallet on this device? This clears local wallet session data and requires seed phrase recovery.',
				'Aftengja veski á þessu tæki? Þetta hreinsar staðbundin veskisgögn og krefst endurheimtar með seed frasa.',
			),
			'recovered': _t(request, 'Wallet recovered.', 'Veski endurheimt.'),
			'wallet': _t(request, 'Wallet', 'Veski'),
			'logout': _t(request, 'Logout', 'Skrá út'),
			'send': _t(request, 'Send', 'Senda'),
			'receive': _t(request, 'Receive', 'Taka á móti'),
			'tx': _t(request, 'Transactions', 'Færslur'),
			'addresses': _t(request, 'Addresses', 'Vistföng'),
			'settings': _t(request, 'Settings', 'Stillingar'),
			'no_wallet': _t(request, 'No active wallet session.', 'Engin virk veskislota.'),
			'status': _t(request, 'Status', 'Staða'),
			'side_balance': _t(request, 'Balance', 'Inneign'),
			'side_wallet_section': _t(request, 'Wallet', 'Veski'),
			'side_session_section': _t(request, 'Session', 'Lota'),
			'disconnect': _t(request, 'Disconnect', 'Aftengja'),
			'electrum_connected': _t(request, 'Electrum connected', 'Electrum tengt'),
			'electrum_disconnected': _t(request, 'Electrum disconnected', 'Electrum ótengt'),
			'topbar_connected': _t(request, 'Connected', 'Tengt'),
			'topbar_disconnected': _t(request, 'Disconnected', 'Ótengt'),
			'server': _t(request, 'Server', 'Server'),
			'latency': _t(request, 'Latency', 'Svar tími'),
			'version': _t(request, 'Version', 'Útgáfa'),
			'balance': _t(request, 'Balance', 'Inneign'),
			'balance_total_wallet': _t(request, 'Total wallet (incl. immature)', 'Heildarveski (með immature)'),
			'balance_total_wallet_short': _t(request, 'Total wallet', 'Heildarveski'),
			'balance_total_wallet_tooltip': _t(request, 'Includes immature funds', 'Inniheldur immature inneign'),
			'to_address': _t(request, 'To address', 'Á vistfang'),
			'source_address': _t(request, 'Source address (optional)', 'Upprunavistfang (valfrjálst)'),
			'source_auto': _t(request, 'Auto (recommended)', 'Sjálfvirkt (ráðlagt)'),
			'source_hint': _t(
				request,
				'Source dropdown shows spendable only.',
				'Dropdown fyrir upprunavistfang sýnir aðeins ráðstöfunarfé.',
			),
			'source_selected_insufficient': _t(
				request,
				'Selected source address does not have enough spendable funds.',
				'Valið upprunavistfang hefur ekki næga ráðstöfunarfjárhæð.',
			),
			'amount': _t(request, 'Amount', 'Upphæð'),
			'fee': _t(request, 'Fee', 'Flutningsgjald'),
			'fee_hint': _t(
				request,
				'Auto uses Medium. Low is cheaper/slower, High is more expensive/faster.',
				'Sjálfgefið er Medium. Low er ódýrara/hægara, High er dýrara/hraðara.',
			),
			'fee_estimated': _t(request, 'Estimated fee', 'Áætlað gjald'),
			'send_now': _t(request, 'Send', 'Senda'),
			'send_ok': _t(request, 'Send broadcasted.', 'Sending broadcastuð.'),
			'send_confirm_title': _t(request, 'Confirm Send', 'Staðfesta sendingu'),
			'send_confirm_cta': _t(request, 'Confirm and Send', 'Staðfesta og senda'),
			'send_cancel': _t(request, 'Cancel', 'Hætta við'),
			'send_missing_confirm': _t(request, 'No pending send to confirm.', 'Engin sending í biðinni til staðfestingar.'),
			'send_invalid_addr': _t(request, 'Invalid FJAR address.', 'Ógilt FJAR vistfang.'),
			'send_invalid_amount': _t(request, 'Invalid amount.', 'Ógild upphæð.'),
			'send_insufficient': _t(request, 'Insufficient balance.', 'Of lítil inneign.'),
			'electrum_offline': _t(request, 'Electrum unavailable', 'Electrum ótiltækt'),
			'tx_none': _t(request, 'No transactions yet.', 'Engar færslur enn.'),
			'wallet_overview_sub': _t(request, 'Quick snapshot of your wallet activity.', 'Stutt yfirlit yfir virkni veskisins.'),
			'wallet_recent_title': _t(request, 'Last 10 Transactions', 'Síðustu 10 færslur'),
			'wallet_view_all_tx': _t(request, 'Open Transactions', 'Opna færslur'),
			'tx_sent': _t(request, 'sent', 'sent'),
			'tx_received': _t(request, 'received', 'received'),
			'tx_change': _t(request, 'change', 'change'),
		},
	}


def _fee_level_or_default(fee_level):
	return fee_level if fee_level in FEE_RATE_SAT_VB else DEFAULT_FEE_LEVEL


def _estimate_fee_sats(fee_level):
	rate = FEE_RATE_SAT_VB.get(_fee_level_or_default(fee_level), FEE_RATE_SAT_VB[DEFAULT_FEE_LEVEL])
	return rate * ESTIMATED_TX_VBYTES


def _format_fjar_from_sats(sats):
	value = Decimal(sats) / Decimal('100000000')
	formatted = format(value, 'f')
	if '.' in formatted:
		formatted = formatted.rstrip('0').rstrip('.')
	return formatted if formatted else '0'


def _find_max_sendable_for_source(seed_phrase, source_address, to_address, fee_level, upper_bound_sats):
	"""Find the largest amount (in sats) that can be prepared from one source address."""
	if upper_bound_sats <= 0:
		return None

	fee_rate = FEE_RATE_SAT_VB.get(_fee_level_or_default(fee_level), FEE_RATE_SAT_VB[DEFAULT_FEE_LEVEL])
	low = 1
	high = int(upper_bound_sats)
	best = None
	iterations = 0

	while low <= high and iterations < 28:
		iterations += 1
		mid = (low + high) // 2
		amount_dec = Decimal(mid) / Decimal('100000000')
		try:
			preview = prepare_send_preview(
				seed_phrase=seed_phrase,
				from_fjar_address=source_address,
				to_fjar_address=to_address,
				amount_fjar=amount_dec,
				fee_rate_sat_vb=fee_rate,
			)
			best = {
				'amount_sats': mid,
				'amount_dec': amount_dec,
				'preview': preview,
			}
			low = mid + 1
		except WalletSendError:
			high = mid - 1

	return best


def _merge_address_histories(address_entries):
	merged = {}
	for entry in address_entries:
		addr = entry.get('address', '')
		for tx in entry.get('history', []) or []:
			txid = tx.get('tx_hash', '')
			if not txid:
				continue
			if txid not in merged:
				merged[txid] = {
					'tx_hash': txid,
					'height': int(tx.get('height', 0) or 0),
					'confirmations': int(tx.get('confirmations', 0) or 0),
					'status': tx.get('status', 'unconfirmed'),
					'amount_sats': 0,
					'timestamp': tx.get('timestamp'),
					'is_coinbase': bool(tx.get('is_coinbase')),
					'addresses': set(),
				}

			m = merged[txid]
			m['amount_sats'] += int(tx.get('amount_sats', 0) or 0)
			m['addresses'].add(addr)
			m['height'] = max(m['height'], int(tx.get('height', 0) or 0))
			m['confirmations'] = max(m['confirmations'], int(tx.get('confirmations', 0) or 0))
			m['is_coinbase'] = m['is_coinbase'] or bool(tx.get('is_coinbase'))
			if m['status'] != 'unconfirmed' and tx.get('status') == 'unconfirmed':
				m['status'] = 'unconfirmed'

	rows = []
	for item in merged.values():
		item['amount_fjar'] = _format_fjar_from_sats(item['amount_sats'])
		item['address_count'] = len(item['addresses'])
		item.pop('addresses', None)
		rows.append(item)

	rows.sort(key=lambda tx: (tx['height'] > 0, -tx['height']))
	return rows


def _to_sats_from_value(value):
	try:
		return int(Decimal(str(value)) * Decimal('100000000'))
	except Exception:  # noqa: BLE001
		return 0


def _annotate_tx_direction(client, merged_txs, wallet_addresses):
	if not merged_txs or not wallet_addresses:
		return

	wallet_script_set = set()
	for addr in wallet_addresses:
		try:
			wallet_script_set.add(client._cashaddr_to_scriptpubkey_hex(addr).lower())
		except Exception:  # noqa: BLE001
			continue

	verbose_cache = {}

	def get_verbose(txid):
		if txid in verbose_cache:
			return verbose_cache[txid]
		try:
			verbose_cache[txid] = client.call('blockchain.transaction.get', [txid, True])
		except Exception:  # noqa: BLE001
			verbose_cache[txid] = None
		return verbose_cache[txid]

	for tx in merged_txs:
		input_from_wallet_sats = 0
		vtx = get_verbose(tx.get('tx_hash', ''))
		if vtx:
			for vin in vtx.get('vin', []) or []:
				prev_txid = vin.get('txid')
				prev_vout = vin.get('vout')
				if prev_txid is None or prev_vout is None:
					continue
				prev_tx = get_verbose(prev_txid)
				if not prev_tx:
					continue
				try:
					prev_out = (prev_tx.get('vout') or [])[int(prev_vout)]
				except Exception:  # noqa: BLE001
					continue
				script_hex = (((prev_out.get('scriptPubKey') or {}).get('hex')) or '').lower()
				if script_hex in wallet_script_set:
					input_from_wallet_sats += _to_sats_from_value(prev_out.get('value', 0))

		wallet_output_sats = int(tx.get('amount_sats', 0) or 0)
		if input_from_wallet_sats > 0:
			tx['direction'] = 'sent'
			sent_sats = max(input_from_wallet_sats - wallet_output_sats, 0)
			tx['sent_amount_fjar'] = _format_fjar_from_sats(sent_sats)
			tx['change_amount_fjar'] = _format_fjar_from_sats(wallet_output_sats)
		else:
			tx['direction'] = 'received'


def _wallet_tabs(context):
	return [
		{'key': 'wallet', 'label': context['labels']['wallet']},
		{'key': 'send', 'label': context['labels']['send']},
		{'key': 'receive', 'label': context['labels']['receive']},
		{'key': 'transactions', 'label': context['labels']['tx']},
		{'key': 'addresses', 'label': context['labels']['addresses']},
	]


def _attach_wallet_state(request, context):
	flow_type, _, _ = _get_active_wallet_data(request)
	context['has_active_wallet'] = bool(flow_type)
	if flow_type:
		context['balance_total_fjar'] = request.session.get('sidebar_balance_total_fjar', '--')
		context['topbar_electrum_connected'] = bool(request.session.get('electrum_connected', False))


def _pending_send_cache_key(send_id):
	return f'wallet:pending_send:{send_id}'


def _get_pending_send(request):
	send_id = request.session.get('pending_send_id')
	if not send_id:
		return None
	return cache.get(_pending_send_cache_key(send_id))


def _set_pending_send(request, payload):
	_clear_pending_send(request)
	send_id = secrets.token_urlsafe(24)
	request.session['pending_send_id'] = send_id
	cache.set(_pending_send_cache_key(send_id), payload, timeout=settings.WALLET_CACHE_TTL_SECONDS)


def _clear_pending_send(request):
	send_id = request.session.pop('pending_send_id', None)
	if send_id:
		cache.delete(_pending_send_cache_key(send_id))


def _clear_pending_create_state(request):
	pending_create_id = request.session.get('pending_create_flow_id')
	if pending_create_id:
		cache.delete(f'wallet:create:{pending_create_id}')
	request.session.pop('pending_create_flow_id', None)
	request.session.pop('seed_step_ack', None)
	request.session.pop('wallet_unlock_until', None)
	request.session.pop('pending_seed_words', None)


def _clear_wallet_state(request):
	create_id = request.session.get('wallet_create_flow_id')
	recover_id = request.session.get('wallet_recover_flow_id')
	pending_create_id = request.session.get('pending_create_flow_id')

	if create_id:
		cache.delete(f'wallet:create:{create_id}')
	if recover_id:
		cache.delete(f'wallet:recover:{recover_id}')
	if pending_create_id:
		cache.delete(f'wallet:create:{pending_create_id}')

	request.session.pop('pending_seed_words', None)
	request.session.pop('pending_create_flow_id', None)
	request.session.pop('pending_seed_words', None)
	request.session.pop('seed_step_ack', None)
	request.session.pop('sidebar_balance_total_fjar', None)
	request.session.pop('wallet_unlock_until', None)
	_clear_pending_send(request)


def _get_pending_create_data(request):
	flow_id = request.session.get('pending_create_flow_id')
	if not flow_id:
		return (None, None)
	data = cache.get(f'wallet:create:{flow_id}')
	if not data:
		request.session.pop('pending_create_flow_id', None)
		return (None, None)
	return (flow_id, data)


def _wallet_ref_cookie_age_seconds():
	return int(getattr(settings, 'WALLET_CACHE_TTL_SECONDS', 28800))


def _set_wallet_ref_cookie(response, flow_type, flow_id):
	payload = {'flow_type': flow_type, 'flow_id': flow_id}
	token = signing.dumps(payload, salt='wallet-ref-cookie')
	response.set_cookie(
		WALLET_REF_COOKIE_NAME,
		token,
		max_age=_wallet_ref_cookie_age_seconds(),
		httponly=True,
		samesite='Lax',
		secure=bool(getattr(settings, 'SESSION_COOKIE_SECURE', False)),
	)


def _clear_wallet_ref_cookie(response):
	response.delete_cookie(WALLET_REF_COOKIE_NAME, samesite='Lax')


def _restore_wallet_session_from_cookie(request):
	token = request.COOKIES.get(WALLET_REF_COOKIE_NAME, '')
	if not token:
		return None

	try:
		payload = signing.loads(token, salt='wallet-ref-cookie')
	except BadSignature:
		return None

	flow_type = str(payload.get('flow_type', '') or '')
	flow_id = str(payload.get('flow_id', '') or '')
	if flow_type not in {'create', 'recover'} or not flow_id:
		return None

	cache_key = f'wallet:{flow_type}:{flow_id}'
	data = cache.get(cache_key)
	if not data:
		return None

	if flow_type == 'create':
		request.session['wallet_create_flow_id'] = flow_id
		request.session.pop('wallet_recover_flow_id', None)
	else:
		request.session['wallet_recover_flow_id'] = flow_id
		request.session.pop('wallet_create_flow_id', None)

	return (flow_type, flow_id, data)


def _redirect_with_lang(route_name, lang, extra_params=None):
	base = reverse(route_name)
	params = {'lang': lang}
	if extra_params:
		params.update(extra_params)
	query = urlencode(params)
	return redirect(f'{base}?{query}')


def _url_with_lang(route_name, lang, extra_params=None):
	base = reverse(route_name)
	params = {'lang': lang}
	if extra_params:
		params.update(extra_params)
	return f'{base}?{urlencode(params)}'


def _passkey_rp_id(request):
	host = request.get_host().split(':', 1)[0].strip().lower()
	return host or 'localhost'


def _passkey_expected_origins(request):
	host = request.get_host().strip().lower()
	origins = [f'https://{host}', f'http://{host}']
	if request.is_secure():
		origins = [f'https://{host}']
	return origins


def _json_error(message, status=400):
	return JsonResponse({'ok': False, 'error': message}, status=status)


def _passkey_descriptors(passkeys):
	descriptors = []
	for item in passkeys or []:
		credential_id = str(item.get('id', '') or '').strip()
		if not credential_id:
			continue
		try:
			descriptors.append(PublicKeyCredentialDescriptor(id=base64url_to_bytes(credential_id)))
		except Exception:  # noqa: BLE001
			continue
	return descriptors


def _get_active_wallet_data(request):
	create_id = request.session.get('wallet_create_flow_id')
	recover_id = request.session.get('wallet_recover_flow_id')

	if create_id:
		data = cache.get(f'wallet:create:{create_id}')
		if data:
			return ('create', create_id, data)

	if recover_id:
		data = cache.get(f'wallet:recover:{recover_id}')
		if data:
			return ('recover', recover_id, data)

	restored = _restore_wallet_session_from_cookie(request)
	if restored:
		return restored

	return (None, None, None)


def _unlock_wallet_session(request):
	request.session['wallet_unlock_until'] = int(timezone.now().timestamp()) + int(settings.WALLET_UNLOCK_TTL_SECONDS)


def _is_wallet_unlocked(request):
	unlock_until = int(request.session.get('wallet_unlock_until', 0) or 0)
	return unlock_until > int(timezone.now().timestamp())


def index(request):
	context = _lang_context(request)
	_attach_wallet_state(request, context)
	context['active_nav'] = 'home'
	flow_type, _, _ = _get_active_wallet_data(request)
	if flow_type:
		return _redirect_with_lang('wallet_home', context['lang'], {'tab': 'wallet'})
	return render(request, 'index.html', context)


def create_wallet(request):
	context = _lang_context(request)
	flow_type, _, _ = _get_active_wallet_data(request)
	if flow_type:
		return _redirect_with_lang('wallet_home', context['lang'], {'tab': 'wallet'})

	context['has_active_wallet'] = False
	context['active_nav'] = 'home'
	mnemo = Mnemonic('english')
	selected_seed_words = request.POST.get('seed_words') or request.GET.get('seed_words') or request.session.get('pending_seed_words') or '12'
	if selected_seed_words not in {'12', '24'}:
		selected_seed_words = '12'
	context['seed_words'] = selected_seed_words
	seed_step_ack = bool(request.session.get('seed_step_ack'))
	autocreate_requested = request.method == 'GET' and request.GET.get('autocreate') == '1'
	fresh_open = (
		request.method == 'GET'
		and request.GET.get('autocreate') != '1'
		and request.GET.get('confirm') != '1'
		and not seed_step_ack
	)

	if fresh_open or autocreate_requested:
		_clear_pending_create_state(request)

	pending_flow_id, pending_wallet = _get_pending_create_data(request)
	pending_seed = (pending_wallet or {}).get('seed_phrase', '')
	if pending_flow_id and pending_seed:
		context['created'] = True
		context['seed_phrase'] = pending_seed
		context['seed_words'] = request.session.get('pending_seed_words', selected_seed_words)
		context['continue_url'] = f"{reverse('create_wallet_continue')}?{urlencode({'lang': context['lang']})}"
		context['seed_step_ack'] = bool(request.session.get('seed_step_ack'))
		context['wallet_password'] = ''
		if request.GET.get('confirm') == '1':
			context['confirm_error'] = context['labels']['seed_confirm_required']

	should_generate = request.method == 'POST' or (autocreate_requested and not pending_flow_id)

	if should_generate:
		seed_strength = 256 if selected_seed_words == '24' else 128
		phrase = mnemo.generate(strength=seed_strength)
		flow_id = secrets.token_urlsafe(24)
		cache_key = f'wallet:create:{flow_id}'

		cache.set(
			cache_key,
			{
				'seed_phrase': phrase,
				'lang': context['lang'],
			},
			timeout=settings.WALLET_CACHE_TTL_SECONDS,
		)

		request.session['pending_create_flow_id'] = flow_id
		request.session['pending_seed_words'] = selected_seed_words
		request.session['seed_step_ack'] = False
		context['has_active_wallet'] = False
		context['created'] = True
		context['seed_phrase'] = phrase
		context['seed_words'] = selected_seed_words
		context['continue_url'] = f"{reverse('create_wallet_continue')}?{urlencode({'lang': context['lang']})}"
		context['seed_step_ack'] = False

	return render(request, 'create.html', context)


def create_wallet_continue(request):
	context = _lang_context(request)
	if request.method != 'POST':
		return _redirect_with_lang('create_wallet', context['lang'])

	action = request.POST.get('seed_action', 'ack')
	if action == 'ack':
		request.session['seed_step_ack'] = True
		return _redirect_with_lang('create_wallet', context['lang'])

	if not request.session.get('seed_step_ack'):
		return _redirect_with_lang('create_wallet', context['lang'], {'confirm': '1'})

	wallet_password = request.POST.get('wallet_password', '')
	if len(wallet_password) < 6:
		_pending_flow_id, pending_wallet = _get_pending_create_data(request)
		pending_seed = (pending_wallet or {}).get('seed_phrase', '')
		context['created'] = bool(pending_seed)
		context['seed_phrase'] = pending_seed
		context['seed_words'] = request.session.get('pending_seed_words', '12')
		context['continue_url'] = f"{reverse('create_wallet_continue')}?{urlencode({'lang': context['lang']})}"
		context['seed_step_ack'] = True
		context['wallet_password'] = wallet_password
		context['confirm_error'] = context['labels']['wallet_password_required']
		return render(request, 'create.html', context)

	flow_id = request.session.get('pending_create_flow_id')

	if not flow_id:
		return _redirect_with_lang('create_wallet', context['lang'])

	wallet_data = cache.get(f'wallet:create:{flow_id}')
	if not wallet_data:
		request.session.pop('pending_create_flow_id', None)
		return _redirect_with_lang('create_wallet', context['lang'])

	wallet_data['password_hash'] = make_password(wallet_password)
	cache.set(f'wallet:create:{flow_id}', wallet_data, timeout=settings.WALLET_CACHE_TTL_SECONDS)

	enable_passkey = request.POST.get('enable_passkey') == '1'

	request.session['wallet_create_flow_id'] = flow_id
	request.session.pop('pending_create_flow_id', None)
	request.session.pop('seed_step_ack', None)
	_unlock_wallet_session(request)
	if enable_passkey:
		response = _redirect_with_lang('settings_page', context['lang'], {'setup': 'passkey'})
		_set_wallet_ref_cookie(response, 'create', flow_id)
		return response

	response = _redirect_with_lang('wallet_home', context['lang'], {'tab': 'wallet'})
	_set_wallet_ref_cookie(response, 'create', flow_id)
	return response


def recover_wallet(request):
	context = _lang_context(request)
	_attach_wallet_state(request, context)
	context['active_nav'] = 'home'
	mnemo = Mnemonic('english')
	seed_phrase = ''
	wallet_password = ''

	if request.method == 'POST':
		seed_phrase = ' '.join(request.POST.get('seed_phrase', '').strip().split())
		wallet_password = request.POST.get('wallet_password', '')

		if not seed_phrase:
			context['error'] = _t(request, 'Seed phrase is required.', 'Seed frasi er nauðsynlegur.')
		elif not mnemo.check(seed_phrase):
			context['error'] = _t(request, 'Invalid seed phrase.', 'Ógildur seed frasi.')
		elif len(wallet_password) < 6:
			context['error'] = context['labels']['wallet_password_required']
		else:
			flow_id = secrets.token_urlsafe(24)
			cache_key = f'wallet:recover:{flow_id}'
			cache.set(
				cache_key,
				{
					'seed_phrase': seed_phrase,
					'password_hash': make_password(wallet_password),
					'passkeys': [],
					'lang': context['lang'],
				},
				timeout=settings.WALLET_CACHE_TTL_SECONDS,
			)
			request.session['wallet_recover_flow_id'] = flow_id
			enable_passkey = request.POST.get('enable_passkey') == '1'
			_unlock_wallet_session(request)
			if enable_passkey:
				response = _redirect_with_lang('settings_page', context['lang'], {'setup': 'passkey'})
				_set_wallet_ref_cookie(response, 'recover', flow_id)
				return response
			response = _redirect_with_lang('wallet_home', context['lang'], {'tab': 'wallet'})
			_set_wallet_ref_cookie(response, 'recover', flow_id)
			return response

	context['seed_phrase'] = seed_phrase
	context['wallet_password'] = wallet_password
	return render(request, 'recover.html', context)


def wallet_home(request):
	context = _lang_context(request)
	_attach_wallet_state(request, context)
	tab = request.GET.get('tab', 'wallet')
	valid_tabs = {'wallet', 'send', 'receive', 'transactions', 'addresses'}
	if tab not in valid_tabs:
		tab = 'wallet'

	context.update(
		{
			'tab': tab,
			'active_nav': tab,
			'tabs': _wallet_tabs(context),
		}
	)

	flow_type, flow_id, wallet_data = _get_active_wallet_data(request)
	if not flow_type:
		return _redirect_with_lang('index', context['lang'])

	if wallet_data.get('password_hash') and not _is_wallet_unlocked(request):
		return _redirect_with_lang('unlock_wallet', context['lang'], {'next': 'wallet', 'tab': tab})

	if flow_type == 'create':
		request.session.pop('pending_create_flow_id', None)
	cache_key = f'wallet:{flow_type}:{flow_id}'

	seed_phrase = wallet_data.get('seed_phrase', '')
	addresses = derive_fjar_addresses(seed_phrase, count=8) if seed_phrase else []
	context['addresses'] = addresses
	context['receive_address'] = addresses[0]['address'] if addresses else ''
	context['receive_qr_data_uri'] = _build_receive_qr_data_uri(context['receive_address'])
	context['tx_log'] = wallet_data.get('tx_log', [])
	context['tx_chain'] = []
	context['tx_query'] = request.GET.get('txid', '').strip().lower()
	context['transactions_mode'] = 'chain'
	context['transactions_page_obj'] = None
	context['transactions_total_count'] = 0
	context['balance_immature_fjar'] = '0'
	context['has_immature_balance'] = False
	context['immature_tx_count'] = 0
	context['coinbase_maturity_confirmations'] = COINBASE_MATURITY_CONFIRMATIONS
	context['source_address'] = context['receive_address']
	context['has_passkeys'] = bool(wallet_data.get('passkeys'))

	balance_data = {'confirmed': 0, 'unconfirmed': 0}
	address_entries = []
	context['electrum_online'] = False
	if addresses:
		client = ElectrumClient()
		try:
			for item in addresses:
				addr = item['address']
				addr_balance = client.get_balance_for_cashaddr(addr)
				addr_history = client.get_history_for_cashaddr(addr)
				address_entries.append(
					{
						'index': item['index'],
						'address': addr,
						'confirmed': int(addr_balance.get('confirmed', 0)),
						'unconfirmed': int(addr_balance.get('unconfirmed', 0)),
						'history': addr_history,
					}
				)

			balance_data = {
				'confirmed': sum(e['confirmed'] for e in address_entries),
				'unconfirmed': sum(e['unconfirmed'] for e in address_entries),
			}
			context['tx_chain'] = _merge_address_histories(address_entries)
			context['electrum_online'] = True
		except (ElectrumConnectionError, InvalidAddress, OSError, ValueError):
			context['electrum_online'] = False

	request.session['electrum_connected'] = bool(context['electrum_online'])

	immature_coinbase_sats = sum(
		int(tx.get('amount_sats', 0) or 0)
		for tx in context['tx_chain']
		if tx.get('is_coinbase') and int(tx.get('confirmations', 0) or 0) < COINBASE_MATURITY_CONFIRMATIONS
	)
	spendable_confirmed_sats = max(int(balance_data['confirmed']) - immature_coinbase_sats, 0)
	total_wallet_sats = int(balance_data['confirmed']) + int(balance_data['unconfirmed'])
	total_sats = spendable_confirmed_sats + int(balance_data['unconfirmed'])
	context['balance_confirmed_fjar'] = _format_fjar_from_sats(spendable_confirmed_sats)
	context['balance_unconfirmed_fjar'] = _format_fjar_from_sats(balance_data['unconfirmed'])
	context['balance_immature_fjar'] = _format_fjar_from_sats(immature_coinbase_sats)
	context['balance_total_wallet_fjar'] = _format_fjar_from_sats(total_wallet_sats)
	context['has_immature_balance'] = immature_coinbase_sats > 0
	context['immature_tx_count'] = len(
		[
			tx for tx in context['tx_chain']
			if tx.get('is_coinbase') and int(tx.get('confirmations', 0) or 0) < COINBASE_MATURITY_CONFIRMATIONS
		]
	)
	context['balance_total_fjar'] = _format_fjar_from_sats(total_sats)
	request.session['sidebar_balance_total_fjar'] = context['balance_total_fjar']
	context['chain_unconfirmed_count'] = len([t for t in context['tx_chain'] if t['status'] == 'unconfirmed'])
	if context['electrum_online']:
		try:
			_annotate_tx_direction(
				client=client,
				merged_txs=context['tx_chain'],
				wallet_addresses=[a['address'] for a in addresses],
			)
		except Exception:  # noqa: BLE001
			logger.exception('Failed to annotate tx direction via input ownership.')

	sent_tx_by_id = {}
	for item in context['tx_log']:
		txid = str(item.get('txid', '') or '').strip()
		if txid:
			sent_tx_by_id[txid] = item

	for tx in context['tx_chain']:
		sent_entry = sent_tx_by_id.get(tx.get('tx_hash', ''))
		if not sent_entry:
			continue
		tx['direction'] = 'sent'
		sent_amount_sats = int(Decimal(str(sent_entry.get('amount', '0') or '0')) * Decimal('100000000'))
		tx['sent_amount_fjar'] = _format_fjar_from_sats(sent_amount_sats)
		wallet_output_sats = int(tx.get('amount_sats', 0) or 0)
		tx['change_amount_fjar'] = _format_fjar_from_sats(max(wallet_output_sats - sent_amount_sats, 0))

	if tab == 'transactions':
		tx_query = context['tx_query']
		mode = 'chain' if context['tx_chain'] else 'log'
		if mode == 'chain':
			rows = context['tx_chain']
			if tx_query:
				rows = [tx for tx in rows if tx_query in str(tx.get('tx_hash', '')).lower()]
			paginator = Paginator(rows, TX_PAGE_SIZE)
			page_obj = paginator.get_page(request.GET.get('tx_page', '1'))
			context['tx_chain'] = list(page_obj.object_list)
			context['tx_log'] = []
		else:
			rows = context['tx_log']
			if tx_query:
				rows = [tx for tx in rows if tx_query in str(tx.get('txid', '')).lower()]
			paginator = Paginator(rows, TX_PAGE_SIZE)
			page_obj = paginator.get_page(request.GET.get('tx_page', '1'))
			context['tx_log'] = list(page_obj.object_list)
			context['tx_chain'] = []

		context['transactions_mode'] = mode
		context['transactions_page_obj'] = page_obj
		context['transactions_total_count'] = len(rows)

	address_spendable_rows = []
	for entry in address_entries:
		received_total_sats = sum(int(tx.get('amount_sats', 0) or 0) for tx in entry.get('history', []))
		spendable_confirmed = 0
		if context['electrum_online']:
			try:
				spendable_utxos = client.list_unspent_for_cashaddr(
					entry['address'],
					min_confirmations=1,
					exclude_immature_coinbase=True,
					coinbase_maturity_confirmations=COINBASE_MATURITY_CONFIRMATIONS,
				)
				spendable_confirmed = sum(int(utxo.get('value', 0) or 0) for utxo in spendable_utxos)
			except Exception:  # noqa: BLE001
				entry_immature = sum(
					int(tx.get('amount_sats', 0) or 0)
					for tx in entry.get('history', [])
					if tx.get('is_coinbase') and int(tx.get('confirmations', 0) or 0) < COINBASE_MATURITY_CONFIRMATIONS
				)
				spendable_confirmed = max(int(entry['confirmed']) - entry_immature, 0)
		else:
			entry_immature = sum(
				int(tx.get('amount_sats', 0) or 0)
				for tx in entry.get('history', [])
				if tx.get('is_coinbase') and int(tx.get('confirmations', 0) or 0) < COINBASE_MATURITY_CONFIRMATIONS
			)
			spendable_confirmed = max(int(entry['confirmed']) - entry_immature, 0)
		address_spendable_rows.append(
			{
				'index': entry['index'],
				'address': entry['address'],
				'spendable_confirmed_sats': spendable_confirmed,
				'received_total_sats': received_total_sats,
			}
		)

	address_spendable_rows.sort(key=lambda row: row['spendable_confirmed_sats'], reverse=True)

	selected_fee_level = _fee_level_or_default(request.POST.get('fee_level') if request.method == 'POST' else DEFAULT_FEE_LEVEL)
	estimated_fee_sats = _estimate_fee_sats(selected_fee_level)
	context['fee_level'] = selected_fee_level
	context['fee_rates'] = FEE_RATE_SAT_VB
	context['fee_sats_by_level'] = {level: _estimate_fee_sats(level) for level in FEE_RATE_SAT_VB}
	context['fee_estimated_fjar'] = _format_fjar_from_sats(estimated_fee_sats)
	context['pending_send'] = _get_pending_send(request)
	context['selected_source_address'] = ''
	context['send_debug_enabled'] = settings.WALLET_SEND_DEBUG
	context['send_debug'] = []
	context['send_success'] = request.session.pop('send_success_notice', '')
	if context['send_debug_enabled']:
		context['send_debug'].append('Broadcast mode: Electrum sign + blockchain.transaction.broadcast.')

	context['coin_control_options'] = [
		{
			'index': row['index'],
			'address': row['address'],
			'spendable_fjar': _format_fjar_from_sats(row['spendable_confirmed_sats']),
			'received_total_fjar': _format_fjar_from_sats(row['received_total_sats']),
			'spendable_confirmed_sats': row['spendable_confirmed_sats'],
		}
		for row in address_spendable_rows
		if row['spendable_confirmed_sats'] > 0
	]
	context['max_amount_fjar'] = context['balance_confirmed_fjar']

	if context['pending_send'] and context['pending_send'].get('from_address'):
		context['selected_source_address'] = context['pending_send']['from_address']
		for opt in context['coin_control_options']:
			if opt['address'] == context['selected_source_address']:
				context['max_amount_fjar'] = opt['spendable_fjar']
				break

	if request.method == 'POST' and tab == 'send':
		send_action = request.POST.get('send_action', 'prepare_send')

		if send_action == 'cancel_send':
			_clear_pending_send(request)
			context['pending_send'] = None
			return render(request, 'wallet_home.html', context)

		if send_action == 'confirm_send':
			pending_send = _get_pending_send(request)
			if not pending_send:
				context['send_error'] = context['labels']['send_missing_confirm']
				if context['send_debug_enabled']:
					context['send_debug'].append('confirm_send requested but pending send state was empty.')
				return render(request, 'wallet_home.html', context)

			try:
				result = sign_and_broadcast(
					seed_phrase=seed_phrase,
					from_fjar_address=pending_send.get('from_address') or context['receive_address'],
					prepared=pending_send['prepared'],
				)
			except WalletSendError as exc:
				context['send_error'] = str(exc)
				if context['send_debug_enabled']:
					context['send_debug'].append(f'broadcast failed: {exc}')
				return render(request, 'wallet_home.html', context)

			try:
				txid = result['txid_network']
				tx_log = wallet_data.setdefault('tx_log', [])
				tx_log.insert(
					0,
					{
						'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
						'to': pending_send.get('to_display') or pending_send['to'],
						'amount': pending_send['amount'],
						'fee': pending_send['fee'],
						'fee_level': pending_send['fee_level'],
						'fee_label': pending_send['fee_label'],
						'txid': txid,
						'txid_source': 'electrum',
						'status': 'sent',
					},
				)
				cache.set(cache_key, wallet_data, timeout=settings.WALLET_CACHE_TTL_SECONDS)
			except Exception:  # noqa: BLE001
				# Tx is already broadcasted; do not fail the user flow due to local bookkeeping issues.
				logger.exception('Post-broadcast bookkeeping failed for txid=%s', result.get('txid_network'))
			finally:
				_clear_pending_send(request)
			request.session['send_success_notice'] = context['labels']['send_ok']
			return _redirect_with_lang('wallet_home', context['lang'], {'tab': 'wallet'})

		to_address_raw = request.POST.get('to_address', '').strip()
		to_address = to_address_raw.lower()
		selected_source_address = request.POST.get('source_address', '').strip().lower()
		amount_raw = request.POST.get('amount', '').strip()
		fee_level = _fee_level_or_default(request.POST.get('fee_level', DEFAULT_FEE_LEVEL))
		estimated_fee_sats = _estimate_fee_sats(fee_level)
		context['fee_level'] = fee_level
		context['fee_estimated_fjar'] = _format_fjar_from_sats(estimated_fee_sats)
		context['send_to_address'] = to_address_raw
		context['selected_source_address'] = selected_source_address
		context['send_amount'] = amount_raw
		if selected_source_address:
			for opt in context['coin_control_options']:
				if opt['address'] == selected_source_address:
					context['max_amount_fjar'] = opt['spendable_fjar']
					break
		if context['send_debug_enabled']:
			context['send_debug'].append(f'prepare_send: amount={amount_raw} fee_level={fee_level} estimated_fee_sats={estimated_fee_sats}')

		try:
			to_address = to_fjarcode_cashaddr(to_address_raw)
		except Exception:  # noqa: BLE001
			context['send_error'] = context['labels']['send_invalid_addr']
			if context['send_debug_enabled']:
				context['send_debug'].append(f'invalid address: {to_address_raw}')
			return render(request, 'wallet_home.html', context)

		try:
			amount_dec = Decimal(amount_raw)
			if amount_dec <= 0 or amount_dec.as_tuple().exponent < -8:
				raise InvalidOperation()
		except (InvalidOperation, ValueError):
			context['send_error'] = context['labels']['send_invalid_amount']
			if context['send_debug_enabled']:
				context['send_debug'].append(f'invalid amount: {amount_raw}')
			return render(request, 'wallet_home.html', context)

		amount_sats = int(amount_dec * Decimal('100000000'))
		if amount_sats + estimated_fee_sats > total_sats:
			context['send_error'] = context['labels']['send_insufficient']
			if context['send_debug_enabled']:
				context['send_debug'].append(
					f'insufficient funds: amount_sats({amount_sats}) + fee_sats({estimated_fee_sats}) > total_sats({total_sats})'
				)
			return render(request, 'wallet_home.html', context)

		pending_send = {
			'to': to_address,
			'to_display': to_address_raw,
			'amount': format(amount_dec, 'f'),
			'fee': _format_fjar_from_sats(estimated_fee_sats),
			'fee_level': fee_level,
			'fee_label': FEE_LEVEL_NAME.get(fee_level, FEE_LEVEL_NAME[DEFAULT_FEE_LEVEL]),
		}
		preview = None
		selected_source = None
		if selected_source_address:
			selected_source = next((s for s in address_spendable_rows if s['address'] == selected_source_address), None)
			candidate_sources = [selected_source] if selected_source else []
		else:
			candidate_sources = [s for s in address_spendable_rows if s['spendable_confirmed_sats'] > 0]

		for source in candidate_sources:
			if not source:
				continue
			if not selected_source_address and source['spendable_confirmed_sats'] < amount_sats + estimated_fee_sats:
				continue
			try:
				preview = prepare_send_preview(
					seed_phrase=seed_phrase,
					from_fjar_address=source['address'],
					to_fjar_address=to_address,
					amount_fjar=amount_dec,
					fee_rate_sat_vb=FEE_RATE_SAT_VB[fee_level],
				)
				selected_source = source
				break
			except WalletSendError:
				continue

		if not preview or not selected_source:
			auto_max_result = None
			if selected_source_address and selected_source:
				auto_max_result = _find_max_sendable_for_source(
					seed_phrase=seed_phrase,
					source_address=selected_source['address'],
					to_address=to_address,
					fee_level=fee_level,
					upper_bound_sats=selected_source['spendable_confirmed_sats'],
				)

			if auto_max_result and auto_max_result['amount_sats'] > 0:
				preview = auto_max_result['preview']
				amount_dec = auto_max_result['amount_dec']
				amount_sats = auto_max_result['amount_sats']
				pending_send['amount'] = format(amount_dec, 'f')
				context['send_amount'] = pending_send['amount']
				context['send_success'] = _t(
					request,
					f'Amount adjusted to max spendable for selected source: {_format_fjar_from_sats(amount_sats)} FJAR.',
					f'Upphæð leiðrétt í hæstu sendanlegu upphæð fyrir valið upprunavistfang: {_format_fjar_from_sats(amount_sats)} FJAR.',
				)

		if not preview or not selected_source:
			if selected_source_address:
				context['send_error'] = context['labels']['source_selected_insufficient']
			else:
				context['send_error'] = _t(
					request,
					'No single address has enough spendable funds. Consolidate funds to one address first.',
					'Ekkert eitt vistfang hefur næga ráðstöfunarfjárhæð. Sameinaðu inneign á eitt vistfang fyrst.',
				)
			if context['send_debug_enabled']:
				context['send_debug'].append('prepare failed across all derived source addresses.')
			return render(request, 'wallet_home.html', context)

		pending_send['prepared'] = preview['prepared']
		pending_send['fee'] = _format_fjar_from_sats(preview['fee_sats'])
		pending_send['from_address'] = selected_source['address']
		context['source_address'] = selected_source['address']
		context['fee_estimated_fjar'] = pending_send['fee']
		_set_pending_send(request, pending_send)
		context['pending_send'] = pending_send
		if context['send_debug_enabled']:
			context['send_debug'].append(
				f'prepared tx: inputs={preview["selected_inputs"]} fee_sats={preview["fee_sats"]} '
				f'input_total={preview["input_total_sats"]} output_total={preview["output_total_sats"]}'
			)
			context['send_debug'].append('pending send created in cache; waiting for confirm_send.')

	return render(request, 'wallet_home.html', context)


def unlock_wallet(request):
	context = _lang_context(request)
	_attach_wallet_state(request, context)
	context['has_active_wallet'] = False
	context['active_nav'] = ''
	next_target = request.GET.get('next', 'wallet')
	tab = request.GET.get('tab', 'wallet')
	show_reset_confirm = request.GET.get('confirm_reset') == '1'

	flow_type, _, wallet_data = _get_active_wallet_data(request)
	if not flow_type:
		return _redirect_with_lang('index', context['lang'])

	if not wallet_data.get('password_hash'):
		if next_target == 'status':
			return _redirect_with_lang('status', context['lang'])
		if next_target == 'settings':
			return _redirect_with_lang('settings_page', context['lang'])
		return _redirect_with_lang('wallet_home', context['lang'], {'tab': 'wallet'})

	if request.method == 'POST':
		password = request.POST.get('wallet_password', '')
		if check_password(password, wallet_data.get('password_hash', '')):
			_unlock_wallet_session(request)
			if next_target == 'status':
				return _redirect_with_lang('status', context['lang'])
			if next_target == 'settings':
				return _redirect_with_lang('settings_page', context['lang'])
			return _redirect_with_lang('wallet_home', context['lang'], {'tab': 'wallet'})
		context['error'] = context['labels']['unlock_invalid']

	context['next_target'] = next_target
	context['tab'] = tab
	context['has_passkeys'] = bool(wallet_data.get('passkeys'))
	context['show_reset_confirm'] = show_reset_confirm
	context['reset_confirm_open_url'] = _url_with_lang(
		'unlock_wallet',
		context['lang'],
		{'next': next_target, 'tab': tab, 'confirm_reset': '1'},
	)
	context['reset_confirm_close_url'] = _url_with_lang(
		'unlock_wallet',
		context['lang'],
		{'next': next_target, 'tab': tab},
	)
	return render(request, 'unlock.html', context)


@require_POST
def passkey_register_begin(request):
	flow_type, flow_id, wallet_data = _get_active_wallet_data(request)
	if not flow_type:
		return _json_error('No active wallet session.', status=404)

	if wallet_data.get('password_hash') and not _is_wallet_unlocked(request):
		return _json_error('Wallet is locked.', status=403)

	passkeys = list(wallet_data.get('passkeys') or [])
	rp_id = _passkey_rp_id(request)
	options = generate_registration_options(
		rp_id=rp_id,
		rp_name='FJAR Wallet',
		user_name='wallet-user',
		user_id=f'{flow_type}:{flow_id}'.encode('utf-8'),
		user_display_name='FJAR Wallet User',
		authenticator_selection=AuthenticatorSelectionCriteria(
			authenticator_attachment=AuthenticatorAttachment.PLATFORM,
			resident_key=ResidentKeyRequirement.PREFERRED,
			user_verification=UserVerificationRequirement.REQUIRED,
		),
		exclude_credentials=_passkey_descriptors(passkeys),
	)

	request.session[PASSKEY_REGISTER_CHALLENGE_SESSION_KEY] = bytes_to_base64url(options.challenge)
	request.session.modified = True
	return JsonResponse(json.loads(options_to_json(options)))


@require_POST
def passkey_register_finish(request):
	flow_type, flow_id, wallet_data = _get_active_wallet_data(request)
	if not flow_type:
		return _json_error('No active wallet session.', status=404)

	if wallet_data.get('password_hash') and not _is_wallet_unlocked(request):
		return _json_error('Wallet is locked.', status=403)

	challenge_b64 = request.session.pop(PASSKEY_REGISTER_CHALLENGE_SESSION_KEY, '')
	if not challenge_b64:
		return _json_error('Missing passkey registration challenge.', status=400)

	try:
		payload = json.loads(request.body.decode('utf-8'))
		credential = payload.get('credential')
		if not credential:
			return _json_error('Missing credential payload.', status=400)
		verified = verify_registration_response(
			credential=credential,
			expected_challenge=base64url_to_bytes(challenge_b64),
			expected_rp_id=_passkey_rp_id(request),
			expected_origin=_passkey_expected_origins(request),
			require_user_verification=True,
		)
	except Exception as exc:  # noqa: BLE001
		logger.exception('Passkey registration verify failed.')
		return _json_error(str(exc), status=400)

	passkeys = list(wallet_data.get('passkeys') or [])
	credential_id_b64 = bytes_to_base64url(verified.credential_id)
	if not any(item.get('id') == credential_id_b64 for item in passkeys):
		passkeys.append(
			{
				'id': credential_id_b64,
				'public_key': bytes_to_base64url(verified.credential_public_key),
				'sign_count': int(verified.sign_count or 0),
			}
		)

	wallet_data['passkeys'] = passkeys
	cache.set(f'wallet:{flow_type}:{flow_id}', wallet_data, timeout=settings.WALLET_CACHE_TTL_SECONDS)
	return JsonResponse({'ok': True})


@require_POST
def passkey_auth_begin(request):
	flow_type, _, wallet_data = _get_active_wallet_data(request)
	if not flow_type:
		return _json_error('No active wallet session.', status=404)

	passkeys = list(wallet_data.get('passkeys') or [])
	if not passkeys:
		return _json_error('No passkey configured for this wallet.', status=400)

	options = generate_authentication_options(
		rp_id=_passkey_rp_id(request),
		allow_credentials=_passkey_descriptors(passkeys),
		user_verification=UserVerificationRequirement.PREFERRED,
	)

	request.session[PASSKEY_AUTH_CHALLENGE_SESSION_KEY] = bytes_to_base64url(options.challenge)
	request.session.modified = True
	return JsonResponse(json.loads(options_to_json(options)))


@require_POST
def passkey_auth_finish(request):
	flow_type, flow_id, wallet_data = _get_active_wallet_data(request)
	if not flow_type:
		return _json_error('No active wallet session.', status=404)

	challenge_b64 = request.session.pop(PASSKEY_AUTH_CHALLENGE_SESSION_KEY, '')
	if not challenge_b64:
		return _json_error('Missing passkey authentication challenge.', status=400)

	try:
		payload = json.loads(request.body.decode('utf-8'))
		credential = payload.get('credential')
		if not credential:
			return _json_error('Missing credential payload.', status=400)
		credential_id = str(credential.get('id', '') or '').strip()
		passkeys = list(wallet_data.get('passkeys') or [])
		passkey_record = next((item for item in passkeys if item.get('id') == credential_id), None)
		if not passkey_record:
			return _json_error('Passkey not recognized for this wallet.', status=400)

		verified = verify_authentication_response(
			credential=credential,
			expected_challenge=base64url_to_bytes(challenge_b64),
			expected_rp_id=_passkey_rp_id(request),
			expected_origin=_passkey_expected_origins(request),
			credential_public_key=base64url_to_bytes(str(passkey_record.get('public_key', ''))),
			credential_current_sign_count=int(passkey_record.get('sign_count', 0) or 0),
			require_user_verification=True,
		)
	except Exception as exc:  # noqa: BLE001
		logger.exception('Passkey authentication verify failed.')
		return _json_error(str(exc), status=400)

	passkey_record['sign_count'] = int(verified.new_sign_count or 0)
	wallet_data['passkeys'] = passkeys
	cache.set(f'wallet:{flow_type}:{flow_id}', wallet_data, timeout=settings.WALLET_CACHE_TTL_SECONDS)

	_unlock_wallet_session(request)
	lang = _lang(request)
	next_target = str(payload.get('next_target', 'wallet') or 'wallet')
	tab = str(payload.get('tab', 'wallet') or 'wallet')
	if next_target == 'status':
		redirect_url = _url_with_lang('status', lang)
	elif next_target == 'settings':
		redirect_url = _url_with_lang('settings_page', lang)
	else:
		redirect_url = _url_with_lang('wallet_home', lang, {'tab': tab})

	return JsonResponse({'ok': True, 'redirect_url': redirect_url})


def settings_page(request):
	context = _lang_context(request)
	_attach_wallet_state(request, context)
	context['active_nav'] = 'settings'
	context['tab'] = ''

	flow_type, _, wallet_data = _get_active_wallet_data(request)
	if not flow_type:
		return _redirect_with_lang('index', context['lang'])

	if wallet_data.get('password_hash') and not _is_wallet_unlocked(request):
		return _redirect_with_lang('unlock_wallet', context['lang'], {'next': 'settings'})

	passkeys = list(wallet_data.get('passkeys') or [])
	context['has_passkeys'] = bool(passkeys)
	context['passkey_count'] = len(passkeys)
	context['setup_passkey'] = request.GET.get('setup') == 'passkey'
	return render(request, 'settings.html', context)


def status(request):
	context = _lang_context(request)
	_attach_wallet_state(request, context)
	context['active_nav'] = 'status'
	context['tabs'] = _wallet_tabs(context)
	context['tab'] = ''

	flow_type, _, wallet_data = _get_active_wallet_data(request)
	if flow_type and wallet_data.get('password_hash') and not _is_wallet_unlocked(request):
		return _redirect_with_lang('unlock_wallet', context['lang'], {'next': 'status'})

	client = ElectrumClient()
	servers = client.probe_servers()
	connected = any(item['ok'] for item in servers)
	request.session['electrum_connected'] = bool(connected)

	context['connected'] = connected
	context['servers'] = servers
	return render(request, 'status.html', context)


@require_GET
def electrum_connected_api(request):
	cache_key = 'wallet:electrum_connected:short'
	cached = cache.get(cache_key)
	if cached is None:
		client = ElectrumClient()
		try:
			connected = any(item.get('ok') for item in client.probe_servers())
		except Exception:  # noqa: BLE001
			connected = False
		cache.set(cache_key, bool(connected), timeout=2)
	else:
		connected = bool(cached)

	request.session['electrum_connected'] = bool(connected)
	return JsonResponse(
		{
			'connected': bool(connected),
			'label': _t(request, 'Connected', 'Tengt') if connected else _t(request, 'Disconnected', 'Ótengt'),
		}
	)


@require_POST
def logout_wallet(request):
	lang = _lang(request)
	flow_type, _, _ = _get_active_wallet_data(request)
	request.session.pop('wallet_unlock_until', None)
	_clear_pending_send(request)
	if not flow_type:
		return _redirect_with_lang('index', lang)
	return _redirect_with_lang('unlock_wallet', lang, {'next': 'wallet', 'tab': 'wallet'})


@require_POST
def disconnect_wallet(request):
	lang = _lang(request)
	_clear_wallet_state(request)
	request.session.flush()
	rotate_token(request)
	response = _redirect_with_lang('index', lang)
	_clear_wallet_ref_cookie(response)
	return response
