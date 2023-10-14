# Prompt

You are a testing expert.

**Requirement (written by the project manager)**

We are implementing an account renewal reminder feature in an existing account management system.

**Background**

- An account expires three years to the day after it is opened.

- There is a grace period of six months before the expired account is closed and the account information deleted.

- Three months before the account information is deleted, we want account holders to be reminded that their expired account will be closed and deleted at the end of the grace period, and offer them the opportunity to reactivate the account before that happens.

- The reminder message will be sent out on the reminder date, and will contain the expiry date and the date on which the grace period ends.

- The calculation of the expiry date and the end of the grace period has already been thoroughly tested.

Analyze this spec for completeness.

----



# How a Good Tester Might Respond

Obviously, this spec covers just one little part of a bigger system, so it is not the complete spec. However, I will assume that by completeness you refer to just the account reminder part. I will approach this by a process of questioning:

- Tell me about the whole system? What sort of thing is this?

- Are there different kinds of account? If so, is this true for all kinds?

- Does the account always expire three years after it is opened?
  - Is it possible to renew the account in the first or second year?
  - Should there be an option to auto-renew?
  - What if the account is being heavily used? Does that change anything?
  - If an account is reactivated, does another three-year window begin?

- The “grace period” doesn’t sound like a grace period, but rather just the final 6 months of the 3-year account life. Am I understanding that correctly?

- Is the ability to reactivate the account only available in the last three months of the 3-year cycle?

- Is there only one reminder message?

- Is the reminder message sent in only one way or multiple ways?

- The expiration dated and end of the grace period appear to be the same thing. Am I understanding that correctly?

- You say you are implementing the “account renewal reminder feature.” Does that mean that the account expiration and deletion process is an existing feature that appears to be working as you want it to?

- Are there GDPR requirements or other laws or standards that may apply to the process of deleting user accounts?

- Is it wise to delete an account outright? Perhaps we should archive it for a period of time, instead?