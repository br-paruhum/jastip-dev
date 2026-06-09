from allauth.account.adapter import DefaultAccountAdapter


class AccountAdapter(DefaultAccountAdapter):
    """Sends the HTML 'Click to Confirm' verification email.

    allauth renders templates/account/email/email_confirmation_message.html
    (and .txt) which we override to show a button instead of a raw URL.
    """

    def get_email_confirmation_url(self, request, emailconfirmation):
        return super().get_email_confirmation_url(request, emailconfirmation)
