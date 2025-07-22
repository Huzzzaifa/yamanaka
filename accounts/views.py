from django.shortcuts import render, redirect
from django.contrib.auth.forms import UserCreationForm
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login

def signup_view(request):
    # Check if the request method is POST (i.e., form submitted)
    if request.method == 'POST':
        form = UserCreationForm(request.POST)  # Bind form with POST data
        if form.is_valid():  # Validate the form input
            user = form.save()  # Save the new user to the database
            login(request, user)  # Log in the user immediately after account creation
            messages.success(request, 'Account created successfully! Welcome to your profile.')
            return redirect('accounts:profile')  # Redirect to the user’s profile page
        else:
            messages.error(request, 'Please fix the errors below.')  # Show error message for invalid form
    else:
        form = UserCreationForm()  # If GET request, create an empty form
    # Render the signup page template with the form context
    return render(request, 'accounts/signup.html', {'form': form})


@login_required
def profile_view(request):
    """
    Displays the profile page for the logged-in user.
    """
    user = request.user  # Get the currently logged-in user
    return render(request, 'accounts/profile.html', {'user': user})
