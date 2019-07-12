## Table of Contents

1. [The Caging Requirements](#the-caging-requirements)
2. [Discussion of User Attributes as Discriminators](#discussion-of-user-attributes-as-discriminators)
3. [A Single Query Solution](#a-single-query-solution)
4. [A more Targeted Solution](#a-more-targeted-solution)
5. [A Larger Sample](#a-larger-sample)
6. [Other Example Cages Using Targeted](#other-example-cages-using-targeted)
7. [Frequencies of First and Last Names](#frequencies-of-first-and-last-names)

## The Caging Requirements

An important aspect of a donation is the categorization of an online donor as an NUSA. A donor may donate anonymously, and  the code attempts to determine whether the donor "looks" like an existing user account. The caging requirements were first outlined on the Donations Team project page [Caging Requirements (5 December 2017 jeturcotte)](https://github.com/orgs/NumbersUSA/teams/donate/discussions/8)

1. If the user is logged in, `donation.gift.user_id` gets their ID
2. If they are not logged in, try: match on email OR (firstname AND lastname AND zipcode)
3. If there are no results, it is okay to create a new user
4. If there is only one result, it is okay to attribute to the user found
5. If there is more than one result, lead `donation.gift.user_id` blank, and add to `donation.caging` with a reference to
   `donation.gift.id`
    - When donor is caged, update `donation.gift.user_id` with the caged `user.id` and delete the row from
      `donation.caging`
    - Produce a log of all donations that day (mentioning if it was auto caged either way) and e-mail it to all designated
      recipients (Dan and Jim)

The simple algorithm of the requirements is an excellent start and if an overall 7.5% misclassification of donors is acceptable then it is a very good choice. At 7.5% around 200,000 donors are misclassified out of 2,600,000 users, on the other hand it is 2 times faster than the extended version reported on here. The single query method, on average is about 2.5 seconds, while the extended is 5 seconds. Both methods discussed here, the single query method and the extended method, fulfill the requirements, except for the logging and managing the caged donors after the fact.

## Discussion of User Attributes as Discriminators

The attributes of a user vary in their discriminating value. Here is a short discussion of some observations gained while exploring ultys users:

- The donor's zip code is a good discriminator, however there is redundancy as one might be expected.
   - The challenge is that the same full name may appear more than once in the same zip code, and not be the same user in
     both cases. There are certain names which are very popular, e.g. Rob Campbell and John Murphy. Each one of these
     names occurs 100 or more times in the user database. Their frequency within a given zip code is obviously less.
   - Looking at users with a full name appearing more than 100 times (30) in the user database the following appear more
     than 5 times within the same zip code. There are a total of 27 full names, of which only 2 are not within the Puerto
     Rico zip code. The list does not include last names which are empty strings.
        - John Adamoyurka appears 9 times for the zip code 0 (Puerto Rico or perhaps empty field).
        - David Allemore appears 14 times for the zip code 0.
        - David Martin appears 5 times for the zip code 77375.
        - Charles Millensifer appears 6 times for the zip code 2893.
   - An initial examination shows that most names occurring over a 100 times appear 1 or 2 times in any given zip code
     and those that appear 2 times are typically the same donor. This is why zip code with the full name can be a good
     discriminator. It it appears 2 times it is likely a duplicate user.
   - A donor may have moved, and already exist with the same information in a different zip code. There have been only a
     few cases like this that have surfaced, but they do exist.
- First name, last name, and street address are strong discriminators.
    - Within a zip code the street address often discriminates between family members, or people sharing the same address.
    - Looking at the current ultsys users, the challenge becomes, that duplicated users often have similar addresses. For
      example, the same user might appear with `2602 Lee Ln.`, and again as `2602 Lee Ln`, where the difference is that,
      in one the abbreviation for lane ends with punctuation. This sort of difference can be handled easily, as well
      as differences in capitalization and white space.
    - The donor may have moved down the street, or a family member might be donating with the same address.
    - One will find users donating with different first names, e.g. Bill and William. Using the street address and/or
      phone number is a good way to catch these discrepancies.
- The email of a donor is quite discriminating in determining whether a donor is unique in the current database.
    - Each user in the ultsys database has a unique email address.
    - One difficulty in using the email address as a discriminator is that returning users often use a different email
      address.
    - So if a user's email address is in the database, and a donor is returning using that same email address the
      match is unique.
    - Using `email || ( first_name && last_name && zip_code )` will miss this common case. Here is such an example:
        - Don Adams, 32162, donadams@gmail.com
        - Donald Adams, 32162, dadams@hotmail.com
    - Duplicate users often appear with both a different first name and email address.
    - The domain name is often different, but the user name will typically have similar characters.
- The phone number, is a relatively strong discriminating attribute, when they are included.
    - They can be different for the same user.
    - The same street address may have a family member with the same last name and different phone number, or the
      same phone number.
    - Using phone number and street address is a strong discriminator.

Looking over the ultsys database it is found that, user names in particular, may have leading and trailing spaces. These can be removed. It is also convenient to transform all text to lower case. These operations are easy to perform:

```
set sql_safe_updates = 0
update ultsys_user set billing_first_name = ltrim( billing_first_name );
update ultsys_user set billing_first_name = rtrim( billing_first_name );
update ultsys_user set billing_first_name = lower( billing_first_name );
```

This was completed for first name, last name, email address, and zip code. The process takes maybe 30 to 90 seconds across around 3 ultsys million users, and so is not very expensive.

## A Single Query Solution

Given a dictionary representing the fields of the UserModel the caging process attempts to properly categorize a donor
as: exists, caged, cage, or new. A simple algorithm is to cage on the following query:

```
1. ( billing_email_address == email_address ) || ( billing_first_name == first_name && billing_last_name == last_name && billing_zipcode == zipcode )
```

An initial look at the ultsys database asked the question whether any email addresses were repeated. What was found was that all emails were unique. This suggests that the query over email addresses will classify those who return with the user's original email address. This points to the fact that discrimination of donors must depend upon chained queries, which employ a comparison across several user fields.

## A More Targeted Solution

The targeted solution opens up the query to return all users by last name. Last names are relatively stable attributes for identifying a user. Although, people do change their last name it is not that common, and any such change will typically persist over long periods. The average number of users for a given last name is under 10. The maximum number of users given a last name is 26,575 (Smith). Most last names have less than 2,000 users, and around 98% are unique, i.e. out of a 100 full names only 2 will have been repeated.

With a last name the users are matched across all fields of the donor. The caging does not allow matches on `search address==''` or phone numbers less then 7 digits: 9999999. For streets it is an easy task to remove various differences, e.g. punctuation and spaces.

```
street = ''.join( char for char in street_address.lower() if char not in string.punctuation ).replace( ' ', '' )
```

The single method query takes approximately 2.5 seconds per donor, while the extended averages around 5 seconds.
These averages were obtained during one caging operations over 2,433 randomly chosen users. The worst case scenario is query over the last name Smith. In this case the single query method takes approximately 3.3 seconds and the extended takes 14.4 seconds.

To cage a donor the following process was followed: (1) A donor was chosen at random using `random.randint()` and a dictionary created from the model, (2) the donor was then deleted from the database, (3) after the caging process was completed the user was added back so they we available for caging on the next donor. For the case where 2,433 donors were categorized 100 were caged, or about 4% of the total.

Each caged donor returned all similar users, and so there were 644 users returned. Of the 644 users the single query method caged 491. The extended returned an added 153 users. The single query method missed 24%, however the number of missed classifications is lower.

The extended method builds a matching matrix: `[ first_name, last_name, zip_code, street_address, email, phone_number ]`
where a match on full name and zip code would look like `[ 1, 1, 1, 0, 0, 0 ]`. The first 3 attributes are referred to the base fields, and the last 3 fields are referred to as discriminators.

How the donor compared to the returned users is depicted in the matching table below. The email address does not appear because it is unique across the database and hence will never be matched using the current process. Existing users are selected at random and used as donors after they have been deleted from the database. When they are then caged their email will be unique.

***
|first name|last name|zip|street address|phone number|count|percentage|
|:---------|:--------|:--|:-------------|:-----------|:----|:---------|
|X         |X        |X  |              |            |243  |38        |
|X         |X        |X  |X             |X           |123  |19        |
|          |X        |X  |X             |X           |76   |12        |
|X         |X        |X  |              |X           |70   |11        |
|X         |X        |X  |X             |            |55   |9         |
|          |X        |X  |X             |            |36   |6         |
|          |X        |X  |              |X           |34   |5         |
|          |X        |   |X             |            |3    |0         |
|          |X        |   |              |X           |3    |0         |
|          |         |   |              |Totals      |644  |100       |

_Matching table for first random test_
***

## A Larger Sample

The total number of users caged were 200, and this led to 1,140 users being categorized. The row order has been maintained with the previous table to make it easy to compare percentages, which are quite close to one another.

|first name|last name|zip|street address|phone number|count|percentage|
|:---------|:--------|:--|:-------------|:-----------|:----|:---------|
|X         |X        |X  |              |            |427  |37        |
|X         |X        |X  |X             |X           |186  |16        |
|          |X        |X  |X             |X           |115  |10        |
|X         |X        |X  |              |X           |110  |10        |
|X         |X        |X  |X             |            |135  |12         |
|          |X        |X  |X             |            |98   |9         |
|          |X        |X  |              |X           |53   |5         |
|          |X        |   |X             |            |5    |0         |
|          |X        |   |              |X           |13   |1         |
|          |         |   |              |Totals      |1,140|100       |

_Matching table for second random test_
***

A more detailed look at the results follow. There were 1,140 users caged as either new, exists, or cage.

### Donor as New User

For example, there were 500 donors categorized as new users. Of these 500 there were around 30 hard misclassifications, 125 soft misclassifications, and a combined 155 rows where a donor was misclassified.

***
|Category  |Rows  |Hard misclassifications|Soft misclassifications|combined|
|:---------|:-----|-----------------------|:----------------------|:-------|
|All       |1,140 |0.075                  |0.123                  |0.198   |
|Exists    |500   |0.060                  |0.250                  |0.310   |
|New       |219   |0.256                  |0.068                  |0.324   |
|Cage      |421   |0.000                  |0.000                  |0.000   |

_Percentages of misclassifications_
***

### Donor as Existing User

Consider the donors that were found to exist. There were a total of 500 rows, with 30 hard misclassifications, and 125 soft misclassifications.

***
|Category  |Rows  |Hard misclassifications|Soft misclassifications|combined|
|:---------|:-----|-----------------------|:----------------------|:-------|
|Exists    |500   |30                     |125                    |155     |

_Number of rows misclassified for donors found to exist_
***

- Look at all results from single query method where donor was found to be exists:
    - Total of 500 users by the extended method.
    - 184 rows: [ 1, 1, 1, 0, 0, 0] full name and zip match
        - There were 22 hard misclassifications. See the example below.
        - There were 125 soft misclassifications:
        - Soft misclassifications occur when there is a match on full name and zip, but there isn't enough information to
          claim they are the same person. See an example below.
    - 63 rows: [1, 1, 1, 0, 0, 1] full name and zip match with phone. Street does not match.
        - There were 8 rows where the street address didn't match and these should be caged.
     - 74 rows: [1, 1, 1, 1, 0, 0] full name and zip match with street address.
         - Phone mismatch isn't strong enough to say they are not the same
         - These are strongly the same donor, and so single gets these correct
     - 123 rows: [1, 1, 1, 1, 0, 1] Match on everything but email
         - These exist
         - Single gets them all correct

Here is an example of a hard misclassification.

***
|first name        |last name     |zip code      |address                       |email|phone|
|:-----------------|:-------------|--------------|:-----------------------------|:----|:----|
|matthew           |soren         |84020         |1934 e fielding hill ln |matts@nerospro.com|4252245273|
|matthew           |soren         |84020         |1981 e brookings dr     |nerosllc@gmail.com|8014941066|

_Hard misclassification for a donor categorized as exists [1, 1, 1, 0, 0, 0]_
***

Here is an example of a soft misclassification because of a lack of discriminating factors:

***
|first name        |last name     |zip code      |address                       |email|phone|
|:-----------------|:-------------|--------------|:-----|:----|:----|
|john a.           |thomas        |0             |      |fathomas@mindspring.com |0|
|john a.   |thomas   |0   |  |jatbhnj@aol.com|0|

_Soft misclassification for a donor categorized as exists [1, 1, 1, 0, 0, 0]_
***

Here is an example of a hard misclassification with a match on the phone number:

***
|first name        |last name     |zip code      |address|email|phone|
|:-----------------|:-------------|--------------|:-----|:----|:----|
|michael|oshea|88030|700 clark st#142 |oekim@ebtv.net|5055463541|
|michael|oshea|88030|700 clark st#142|oekim@webtv.net|5055463541|

_Hard misclassification for a donor categorized as exists [1, 1, 1, 0, 0, 1]_
***

### Donor as New User

Consider the donors that were found to be new. There were a total of 219 rows, with 56 hard misclassifications, and 15 soft misclassifications.

***
|Category  |Rows  |Hard misclassifications|Soft misclassifications|combined|
|:---------|:-----|-----------------------|:----------------------|:-------|
|New       |219   |56                     |15                     |71      |

_Number of rows misclassified for donors found to be new_
***

- Look at all results from single query method where donor was found to be new:
    - Total of 219 users by the extended method
    - 56 were hard misclassifications by single method. See an example below.
    - 15 were soft misclassifications. See an example below.
        - The question is are these separate accounts?

***
|first name        |last name     |zip code      |address                       |email|phone|
|:-----------------|:-------------|--------------|:-----|:----|:----|
|ruchard g.|varna|33760|eastwood shores 2907 lichen ln|moshue2@rcoketmail.com|7275361150|
|richard g.|varna|33760|eastwood shores 2907 lichen ln|moshue2@rocketmail.com|7275361150|

_Hard misclassification for a donor categorized as new [0, 1, 1, 1, 0, 1]_
***

***
|first name        |last name     |zip code      |address                       |email|phone|
|:-----------------|:-------------|--------------|:-----|:----|:----|
|wayne and mary|patton|37221|525 westward winds drive|waynepatton@comcast.net   |6156464543|
|wayne         |patton|37221|525 westward winds dr.  |waynepatton1@bellsouth.net|6156464543|

_Soft misclassification for a donor categorized as new [0, 1, 1, 1, 0, 1]_
***

### Donor as Cage User

There can be no misclassifications for results that need to be caged. There were a total of 421 users caged by the extended method. The extended requires an exact match, including a match on email, and this may be too strict. So unless, your email is in the DB it will cage you. All emails in the DB are unique and so you will always be caged by the extended method unless you exist. It might be better to look for matches where `email_address || phone_number`

## Other Example Cages Using Targeted

Here are several donors paired with their caged result, which demonstrate the diverse cases the Targeted Method is capable of capturing.

***
|email                       |full name      |street address               |zip  |phone number|
|:---------------------------|:--------------|:----------------------------|:----|:-----------|
|promise@server.net          |KC Anonn       |123 Maple                    |32202|9044444444  |
|sunrise@harbor.com          |KC Anon        |123 Maple                    |33928|9729925830  |

_Interesting examples 1_
***

***
|email                       |full name      |street address               |zip  |phone number|
|:---------------------------|:--------------|:----------------------------|:----|:-----------|
|AAonmesa@juno.com           |Alexander Brown|261 Hazel Lane               |93444|8059293310  |
|a-brown@webtv.net           |Al Brown       |261 Hazel Lane               |93444|8059293310  |

_Interesting examples 2_
***

***
|email                       |full name      |street address               |zip  |phone number|
|:---------------------------|:--------------|:----------------------------|:----|:-----------|
|ajansbach@live.com          |ARLENE ANSBACH |N FARLEY AVE                 |64157|8165208402  |
|ajanasbach@live.com         |Arlene Ansbach |N Farley Ave                 |64157|8165207182  |

_Interesting examples 3_
***

***
|email                       |full name      |street address               |zip  |phone number|
|:---------------------------|:--------------|:----------------------------|:----|:-----------|
|crannis@adelphia.net        |Vernon Annis   |244 Holbrook Bay Commons D35 |5857 |8023341653  |
|annisvp@comcast.net         |Vernon Annis   |244 Holbrook Bay Commons D3-5|58570|8023341653  |

_Interesting examples 4_
***

***
|email                       |full name      |street address               |zip  |phone number|
|:---------------------------|:--------------|:----------------------------|:----|:-----------|
|beverleecoxmontana@gmail.com|Bev Cox        |877 Haring Lane              |32757|4068504878  |
|beverlee@cablemt.net        |Bev Cox        |244 1875 Pheasant Brook Dr   |59044|4068504878  |

_Interesting examples 5_
***

***
|email                       |full name      |street address               |zip  |phone number|
|:---------------------------|:--------------|:----------------------------|:----|:-----------|
|cecox04@yahoo.con           |Carolyn Cox    |5392 Fm 226                  |75961|9365640625  |
|cecox04@yahoo.com           |Carolyn Cox    |5148 Fm 226                  |75978|9365640625  |

_Interesting examples 6_
***

## Frequencies of First and Last Names

In a 3 million sample of ultsys users the email address was available for each user and was unique. A combination of first and last names are not quite that discriminating, but nonetheless quite good, as the table below demonstrates. Phone numbers and addresses could be examined in the same way.

***
|Attribute                            |Value     |
|:------------------------------------|:---------|
|Number of users sampled              | 3000000  |
|Number of users without names        | 268408   |
|Number of_users with names           | 2731592  |
|Percentage of users with names       | 91%      |
|Number first names with no last name | 8090     |
|Total number of last names           | 2731592  |
|Number of unique last names          | 2669462  |
|Number of non-unique last names      | 62130    |
|Percentage of unique last names      | 98%      |

_Ultsys Users First and Last Name Frequencies_
